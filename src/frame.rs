//! Physical frame allocator: intrusive free list over `[__heap_end, RAM_END)`.
//!
//! Owns every 4 KiB frame above the linker heap carve-out (D-0024,
//! `__heap_start`..`__heap_end`). Each free frame stores the next-free
//! pointer in its own first 8 bytes; the only metadata in `.bss` is the list
//! head (D-0019). Page tables, and later task stacks, come from here. Without
//! it there is no allocatable physical memory beyond the static image.

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

static mut HEAD: usize = 0;
static mut HEAP_END: usize = 0;
static mut TOTAL: usize = 0;
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
/// the DTB is clobberable — `init` will write next-pointers through it and
/// `alloc_frame` may hand those pages out.
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
}

/// Link every frame in `[__heap_end, RAM_END)` into the free list.
/// Heap bounds come from the linker (D-0024); do not re-derive them.
/// Call `check_dtb` first (D-0023).
pub fn init() {
    let start = heap_start();
    let end = heap_end();
    if start % PAGE_SIZE != 0 || end % PAGE_SIZE != 0 || end <= start || end > RAM_END {
        panic!("heap symbols unusable: start={:#x} end={:#x}", start, end);
    }
    unsafe {
        HEAP_END = end;
        HEAD = 0;
        TOTAL = 0;
    }

    let mut pa = end;
    let mut n = 0usize;
    while pa < RAM_END {
        unsafe { core::ptr::write(pa as *mut usize, HEAD) };
        unsafe { HEAD = pa };
        pa += PAGE_SIZE;
        n += 1;
    }
    unsafe { TOTAL = n };
}

pub fn total_frames() -> usize {
    unsafe { TOTAL }
}

/// Length of the free list. Read-only: does not allocate or free.
pub fn free_count() -> usize {
    let mut n = 0usize;
    let mut pa = unsafe { HEAD };
    let cap = unsafe { TOTAL }.saturating_add(1);
    while pa != 0 {
        n += 1;
        if n > cap {
            panic!("frame free list looks cyclic after {} nodes", n);
        }
        pa = unsafe { core::ptr::read(pa as *const usize) };
    }
    n
}

/// D-0036: after this, `alloc_frame` / `free_frame` panic printing the
/// request. Called immediately before the first `sret` to U. Prints
/// `frames frozen: free=N`.
#[cfg_attr(
    any(
        feature = "panic-selftest",
        feature = "hang-selftest",
        feature = "stress",
        feature = "frame-exhaust-selftest"
    ),
    allow(dead_code)
)]
pub fn freeze() {
    unsafe { FROZEN = true };
    println!("frames frozen: free={}", free_count());
}

/// Pop the head, **then** zero the frame. The next-pointer lives in the
/// first 8 bytes; it must be copied into `HEAD` before `write_bytes` wipes
/// it. Zero-on-free would erase that pointer while the frame is still on
/// the list.
pub fn alloc_frame() -> usize {
    if unsafe { FROZEN } {
        panic!("alloc_frame after freeze");
    }
    let pa = unsafe { HEAD };
    if pa == 0 {
        panic!("out of frames (total {})", unsafe { TOTAL });
    }
    let next = unsafe { core::ptr::read(pa as *const usize) };
    unsafe { HEAD = next };
    unsafe { core::ptr::write_bytes(pa as *mut u8, 0, PAGE_SIZE) };
    pa
}

/// Push `pa` as the new head. The cheap double-free check is `pa == HEAD`:
/// it catches freeing the same frame twice with no intervening alloc
/// (the second push would make a 1-cycle list). It does **not** scan the
/// list, so it misses free-A / free-B / free-A and free-A / alloc / free-A
/// (the latter is a live frame being freed, not a double-free of a listed
/// one). O(n) would catch those; we are not doing O(n).
pub fn free_frame(pa: usize) {
    if unsafe { FROZEN } {
        panic!("free_frame after freeze {:#x}", pa);
    }
    if pa % PAGE_SIZE != 0 || pa < unsafe { HEAP_END } || pa >= RAM_END {
        panic!("free_frame: {:#x} is not a managed frame", pa);
    }
    if pa == unsafe { HEAD } {
        panic!("double-free of {:#x} (already head)", pa);
    }
    unsafe {
        core::ptr::write(pa as *mut usize, HEAD);
        HEAD = pa;
    }
}

/// Allocate two, free the first, get it back (LIFO). Prints the total and
/// `FRAME OK`.
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
    println!(
        "frames {} heap_start={:#x} heap_end={:#x} ram_end={:#x}",
        total_frames(),
        heap_start(),
        unsafe { HEAP_END },
        RAM_END
    );
    println!("FRAME OK");
}
