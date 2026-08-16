//! U-mode app: HTTP/1.0 one-shot (D-0053) or UDP echo (`udp-echo`).
//!
//! Owns the demo that runs over `recv`/`send`. HTTP: print `HTTP READY`,
//! spin on `recv` (that spin *is* the poll loop), parse a GET in the
//! **one segment** returned, `send` a fixed 200 with `Connection: close`
//! and FIN, wait for EOF, `exit`. `persist` recycles that loop after EOF
//! so `just run-http` can serve sequential connections (not keep-alive).
//! UDP echo is the T3.9 sibling. No `println!`, no `format!`, no
//! `f32`/`f64`, no `panic!`. Abort is `usys::exit` / `unimp` so the path
//! stays in `.utext`.

#![no_std]
#![no_builtins]

#[cfg(all(feature = "persist", feature = "udp-echo"))]
compile_error!("persist is HTTP-only");

#[cfg(feature = "udp-echo")]
mod inner {
    #[link_section = ".urodata"]
    pub static READY: [u8; 15] = *b"UDP ECHO READY\n";
    #[link_section = ".urodata"]
    pub static DONE: [u8; 11] = *b"NET UDP OK\n";
    /// Matches `net::UDP_PAYLOAD_MAX`; kernel `const`-assert ties them
    /// (D-0056 / finding 36).
    pub const BUF: usize = 1472;
}

#[cfg(not(feature = "udp-echo"))]
mod inner {
    #[link_section = ".urodata"]
    pub static READY: [u8; 11] = *b"HTTP READY\n";
    #[cfg(not(feature = "persist"))]
    #[link_section = ".urodata"]
    pub static DONE: [u8; 10] = *b"HTTP DONE\n";
    /// `HTTP/1.0 200 OK` + `Connection: close` + body `whimbrel\n` (D-0053).
    #[link_section = ".urodata"]
    pub static RESP: [u8; 92] = *b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\nContent-Length: 9\r\n\r\nwhimbrel\n";
    /// Matches `tcp::PAYLOAD_MAX`; kernel `const`-assert ties them
    /// (D-0056 / finding 36).
    pub const BUF: usize = 512;
}

/// Recv buffer length exported so the kernel can `const`-assert it
/// against `tcp::PAYLOAD_MAX` / `net::UDP_PAYLOAD_MAX` (D-0056).
pub const RECV_BUF: usize = inner::BUF;

/// U-mode entry. `#[no_mangle]` so `check-utext` can require this
/// symbol to sit in `.utext`, not kernel `.text`.
#[no_mangle]
#[link_section = ".utext"]
pub extern "C" fn app_main() -> ! {
    unsafe {
        let _ = usys::write(inner::READY.as_ptr(), inner::READY.len());
    }

    let mut buf: core::mem::MaybeUninit<[u8; inner::BUF]> = core::mem::MaybeUninit::uninit();
    let ptr = buf.as_mut_ptr() as *mut u8;

    #[cfg(feature = "udp-echo")]
    {
        loop {
            let (err, n) = unsafe { usys::recv(ptr, inner::BUF) };
            if err == usys::ERR_AGAIN {
                continue;
            }
            if err != usys::OK {
                usys::exit(1);
            }
            if n > inner::BUF {
                usys::exit(1);
            }
            let (serr, _) = unsafe { usys::send(ptr as *const u8, n, 0) };
            if serr != usys::OK {
                usys::exit(2);
            }
            unsafe {
                let _ = usys::write(inner::DONE.as_ptr(), inner::DONE.len());
            }
            usys::exit(0);
        }
    }

    #[cfg(not(feature = "udp-echo"))]
    {
        loop {
            let (err, n) = unsafe { usys::recv(ptr, inner::BUF) };
            if err == usys::ERR_AGAIN {
                continue;
            }
            if err != usys::OK {
                usys::exit(1);
            }
            if n == 0 {
                #[cfg(feature = "persist")]
                {
                    continue;
                }
                #[cfg(not(feature = "persist"))]
                {
                    unsafe {
                        let _ = usys::write(inner::DONE.as_ptr(), inner::DONE.len());
                    }
                    usys::exit(0);
                }
            }
            if n > inner::BUF {
                usys::exit(1);
            }
            if !request_ok(ptr, n) {
                usys::exit(3);
            }
            let (serr, _) =
                unsafe { usys::send(inner::RESP.as_ptr(), inner::RESP.len(), usys::SEND_FIN) };
            if serr != usys::OK {
                usys::exit(2);
            }
        }
    }
}

/// One-segment request: `GET ` and a `\r\n` in this buffer. No
/// cross-segment assembly (D-0053).
#[cfg(not(feature = "udp-echo"))]
#[link_section = ".utext"]
fn request_ok(p: *const u8, n: usize) -> bool {
    if n < 6 {
        return false;
    }
    unsafe {
        if *p != b'G' || *p.add(1) != b'E' || *p.add(2) != b'T' || *p.add(3) != b' ' {
            return false;
        }
        let mut i = 0;
        while i + 1 < n {
            if *p.add(i) == b'\r' && *p.add(i + 1) == b'\n' {
                return true;
            }
            i += 1;
        }
    }
    false
}

#[inline(never)]
#[link_section = ".utext"]
pub fn abort() -> ! {
    usys::exit(255)
}
