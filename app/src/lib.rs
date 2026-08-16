//! UDP echo app in U-mode (D-0044, D-0051).
//!
//! Owns the T3.8 echo once it moves out of the kernel: print
//! `UDP ECHO READY`, spin on `recv` (that spin *is* the poll loop;
//! D-0035 / D-0040), `send` the payload back, print `NET UDP OK`,
//! `exit`. No `println!`, no `format!`, no `f32`/`f64`, no `panic!`.
//! Abort is `usys::exit` / `unimp` so the path stays in `.utext`.
//! Without this crate the echo would remain a kernel auto-reply and
//! T3.9 would not have compiled Rust in the user sections.

#![no_std]
#![no_builtins]

/// Named `.urodata` so we do not depend on matching LLVM's unique
/// `.rodata..Lanon.*` section names across the rlib boundary (D-0051).
#[link_section = ".urodata"]
static READY: [u8; 15] = *b"UDP ECHO READY\n";
#[link_section = ".urodata"]
static DONE: [u8; 11] = *b"NET UDP OK\n";
/// Bigger than the T3.8 payload (`whimbrel-udp-echo`); small enough
/// that LLVM should emit stores, not a `memset` call (D-0051).
const BUF: usize = 32;

/// U-mode entry. `#[no_mangle]` so `check-utext` can require this
/// symbol to sit in `.utext`, not kernel `.text`.
#[no_mangle]
#[link_section = ".utext"]
pub extern "C" fn app_main() -> ! {
    unsafe {
        let _ = usys::write(READY.as_ptr(), READY.len());
    }

    // Not zeroed: `recv` overwrites the prefix it returns. A
    // `[0u8; N]` here is how LLVM decides to call `memset`.
    let mut buf: core::mem::MaybeUninit<[u8; BUF]> = core::mem::MaybeUninit::uninit();
    let ptr = buf.as_mut_ptr() as *mut u8;

    loop {
        let (err, n) = unsafe { usys::recv(ptr, BUF) };
        if err == usys::ERR_AGAIN {
            continue;
        }
        if err != usys::OK {
            usys::exit(1);
        }
        if n > BUF {
            usys::exit(1);
        }
        let (serr, _) = unsafe { usys::send(ptr as *const u8, n, 0) };
        if serr != usys::OK {
            usys::exit(2);
        }
        unsafe {
            let _ = usys::write(DONE.as_ptr(), DONE.len());
        }
        usys::exit(0);
    }
}

/// Not a lang item (D-0051): the image's `#[panic_handler]` must be
/// fetchable from S-mode, so it lives in kernel `.text`. This is the
/// U-mode abort if we ever call it by hand. A rustc-inserted `panic!`
/// would `jal` into `core::panicking` in kernel `.text` and fail
/// `check-utext` — that is the gate, not this function.
#[inline(never)]
#[link_section = ".utext"]
pub fn abort() -> ! {
    usys::exit(255)
}
