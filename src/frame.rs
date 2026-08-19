//! Physical frame allocator: bump pointer plus a recycled intrusive list.
//!
//! Owns every 4 KiB frame above the linker heap carve-out (D-0024,
//! `__heap_start`..`__heap_end`). Virgin frames are a bump (D-0065);
//! recycled frames store the next-free pointer in their first 8 bytes
//! (D-0019, amended). Page tables, and later task stacks, come from here.
//! Without it there is no allocatable physical memory beyond the static
//! image.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::println;

/// QEMU `virt` RAM base. `hw/riscv/virt.c`; D-0012.
pub const RAM_START: usize = 0x8000_0000;
/// QEMU `virt` default 128 MiB. D-0012, D-0023. `justfile` never passes `-m`.
pub const RAM_END: usize = 0x8800_0000;
/// Sv39 page size.
pub const PAGE_SIZE: usize = 4096;
/// Flattened DT magic, big-endian. Devicetree spec §5.2.
const DTB_MAGIC: u32 = 0xd00d_feed;

extern "C" {
    static __heap_start: u8;
    static __heap_end: u8;
}

/// Recycled-list head. 0 means empty. Intrusive next-pointer in the frame.
static mut HEAD: usize = 0;
/// Next virgin frame in `[HEAP_END, RAM_END)`.
static mut BUMP: usize = 0;
static mut HEAP_END: usize = 0;
static mut TOTAL: usize = 0;
/// Length of the recycled list. `free_count` is arithmetic (D-0065).
static mut RECYCLED: usize = 0;
static mut FROZEN: bool = false;

fn heap_start() -> usize {
    core::ptr::addr_of!(__heap_start) as usize
}

fn heap_end() -> usize {
    core::ptr::addr_of!(__heap_end) as usize
}

fn read_be_u32(pa: usize) -> u32 {
    let p = pa as *const u8;
    u32::from_be_bytes(unsafe { [*p, *p.add(1), *p.add(2), *p.add(3)] })
}

/// D-0023: validate the DTB header **before** `init`. The blob sits at
/// `0x87e0_0000`, inside `[heap_end, RAM_END)`. After this check returns,
/// the DTB is clobberable — `alloc_frame` may hand those pages out when
/// the bump reaches them. Init itself does not write through the blob.
pub fn check_dtb(dtb_pa: usize) {
    let magic = read_be_u32(dtb_pa);
    let totalsize = read_be_u32(dtb_pa + 4) as usize;
    let end = match dtb_pa.checked_add(totalsize) {
        Some(e) => e,
        None => panic!(
            "DTB wrap: pa={:#x} totalsize={:#x} magic={:#x}",
            dtb_pa, totalsize, magic
        ),
    };
    if magic != DTB_MAGIC || dtb_pa < RAM_START || end > RAM_END {
        panic!(
            "DTB header: magic={:#x} (want {:#x}) pa={:#x} totalsize={:#x} end={:#x}",
            magic, DTB_MAGIC, dtb_pa, totalsize, end
        );
    }
    // D-0079 verify-item (a): D-0065's clobberable-DTB assumption as an
    // assert, not a hope. Clobbering is legal only because the blob
    // sits inside the bump range [heap_end, RAM_END); a loader that
    // placed it below heap_end would have it corrupted by the kernel
    // image or the heap. Verified 0x87e0_0000 in both lanes (QEMU
    // places it; OpenSBI passes it through).
    if dtb_pa < heap_end() {
        panic!(
            "DTB at {:#x} below heap_end {:#x}: outside the D-0065 clobber range",
            dtb_pa,
            heap_end()
        );
    }
}

/// Arm the bump over `[__heap_end, RAM_END)`. Heap bounds come from the
/// linker (D-0024); do not re-derive them. Call `check_dtb` first (D-0023).
pub fn init() {
    let start = heap_start();
    let end = heap_end();
    if start % PAGE_SIZE != 0 || end % PAGE_SIZE != 0 || end <= start || end > RAM_END {
        panic!("heap symbols unusable: start={:#x} end={:#x}", start, end);
    }
    if (RAM_END - end) % PAGE_SIZE != 0 {
        panic!(
            "managed range not page-sized: end={:#x} ram_end={:#x}",
            end, RAM_END
        );
    }
    unsafe {
        HEAP_END = end;
        BUMP = end;
        HEAD = 0;
        RECYCLED = 0;
        TOTAL = (RAM_END - end) / PAGE_SIZE;
    }
}

pub fn total_frames() -> usize {
    unsafe { TOTAL }
}

/// Available frames: virgin remainder plus recycled length. O(1).
#[cfg_attr(
    all(feature = "no-sret", not(feature = "freeze-selftest")),
    allow(dead_code)
)]
pub fn free_count() -> usize {
    let bump = unsafe { BUMP };
    if bump > RAM_END {
        panic!("frame bump {:#x} past RAM_END", bump);
    }
    (RAM_END - bump) / PAGE_SIZE + unsafe { RECYCLED }
}

/// D-0036: after this, `alloc_frame` / `free_frame` panic printing the
/// request. Called immediately before the first `sret` to U. Prints
/// `frames frozen: free=N`. Semantics unchanged under D-0065: the bool
/// is the freeze; `free_count()` is just the printed number.
#[cfg_attr(
    all(feature = "no-sret", not(feature = "freeze-selftest")),
    allow(dead_code)
)]
pub fn freeze() {
    unsafe { FROZEN = true };
    println!("frames frozen: free={}", free_count());
}

/// Recycled list first (LIFO), then the bump. Zero after the next-pointer
/// is copied out of a recycled frame.
pub fn alloc_frame() -> usize {
    if unsafe { FROZEN } {
        panic!("alloc_frame after freeze");
    }
    let recycled = unsafe { HEAD };
    let pa = if recycled != 0 {
        let next = unsafe { core::ptr::read(recycled as *const usize) };
        let n = unsafe { RECYCLED };
        if n == 0 {
            panic!("recycled underflow while HEAD={:#x}", recycled);
        }
        unsafe {
            HEAD = next;
            RECYCLED = n - 1;
        }
        recycled
    } else {
        let bump = unsafe { BUMP };
        if bump >= RAM_END {
            panic!("out of frames (total {})", unsafe { TOTAL });
        }
        unsafe { BUMP = bump + PAGE_SIZE };
        bump
    };
    unsafe { core::ptr::write_bytes(pa as *mut u8, 0, PAGE_SIZE) };
    pa
}

/// Push `pa` as the new recycled head. The cheap double-free check is
/// `pa == HEAD`: it catches freeing the same frame twice with no
/// intervening alloc. It does **not** scan the list. O(n) would catch
/// those; we are not doing O(n). A frame at or above `BUMP` was never
/// handed out.
#[cfg(any(not(feature = "fast-boot"), feature = "stress"))]
pub fn free_frame(pa: usize) {
    if unsafe { FROZEN } {
        panic!("free_frame after freeze {:#x}", pa);
    }
    if pa % PAGE_SIZE != 0 || pa < unsafe { HEAP_END } || pa >= RAM_END {
        panic!("free_frame: {:#x} is not a managed frame", pa);
    }
    if pa >= unsafe { BUMP } {
        panic!("free_frame: {:#x} is above bump {:#x}", pa, unsafe { BUMP });
    }
    if pa == unsafe { HEAD } {
        panic!("double-free of {:#x} (already head)", pa);
    }
    unsafe {
        core::ptr::write(pa as *mut usize, HEAD);
        HEAD = pa;
        RECYCLED += 1;
    }
}

/// Walk the recycled list; panics on cycle or walk ≠ `RECYCLED`.
#[cfg(any(not(feature = "fast-boot"), feature = "stress"))]
pub fn check_recycled() {
    let mut n = 0usize;
    let mut pa = unsafe { HEAD };
    let cap = unsafe { RECYCLED }.saturating_add(1);
    while pa != 0 {
        n += 1;
        if n > cap {
            panic!("recycled list looks cyclic after {} nodes", n);
        }
        pa = unsafe { core::ptr::read(pa as *const usize) };
    }
    let want = unsafe { RECYCLED };
    if n != want {
        panic!("recycled walk {} count {}", n, want);
    }
}

/// Allocate two, free the first, get it back (LIFO). Prints the total and
/// `FRAME OK`. Deliberately leaves `b` and `c` allocated: those two are
/// still outstanding at `freeze()` (D-0036) and are not a leak to plug.
#[cfg(not(feature = "fast-boot"))]
pub fn self_test() {
    let a = alloc_frame();
    let b = alloc_frame();
    if a == b {
        panic!("alloc returned {:#x} twice", a);
    }
    if a % PAGE_SIZE != 0 || b % PAGE_SIZE != 0 {
        panic!("unaligned frames {:#x} {:#x}", a, b);
    }
    let a_page = unsafe { core::slice::from_raw_parts(a as *const u8, PAGE_SIZE) };
    let b_page = unsafe { core::slice::from_raw_parts(b as *const u8, PAGE_SIZE) };
    if a_page.iter().any(|&x| x != 0) || b_page.iter().any(|&x| x != 0) {
        panic!("frame not zeroed: a={:#x} b={:#x}", a, b);
    }
    free_frame(a);
    let c = alloc_frame();
    if c != a {
        panic!("LIFO broken: freed {:#x}, got {:#x}", a, c);
    }
    check_recycled();
    if free_count() != total_frames() - 2 {
        panic!(
            "self_test accounting: free={} total={} want total-2",
            free_count(),
            total_frames()
        );
    }
    println!(
        "frames {} heap_start={:#x} heap_end={:#x} ram_end={:#x}",
        total_frames(),
        heap_start(),
        unsafe { HEAP_END },
        RAM_END
    );
    println!("FRAME OK");
}
