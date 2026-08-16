//! Kernel heap: first-fit free list over `[__heap_start, __heap_end)`.
//!
//! Owns every variable-size block in the 1 MiB carve-out (D-0024) and is
//! the `#[global_allocator]`. `Box` / `Vec` / `String` have nowhere to live
//! without it. The list is address-sorted and coalesced on free (D-0027).
//! Exhaustion panics with size and align; the interrupt path must not
//! allocate (timer `println!` does not).

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

#[cfg(not(feature = "fast-boot"))]
use crate::println;
use core::alloc::{GlobalAlloc, Layout};
use core::ptr;

extern "C" {
    static __heap_start: u8;
    static __heap_end: u8;
}

/// Header immediately before the user pointer. `size` is the whole block
/// including any absorbed prefix (`pad`) and a too-small tail. Free: `extra`
/// is the next-free pointer. Used: `extra` is `pad` (bytes before this
/// header that belong to the block).
#[repr(C)]
struct Header {
    size: usize,
    extra: usize,
}

const HEADER: usize = core::mem::size_of::<Header>();
const HEADER_ALIGN: usize = core::mem::align_of::<Header>();
/// Smallest block that can sit on the free list (a header, no payload).
const MIN_FREE: usize = HEADER;

const _: () = assert!(HEADER == 16);
const _: () = assert!(HEADER_ALIGN == 8);

struct Heap;

#[global_allocator]
static ALLOC: Heap = Heap;

static mut HEAD: *mut Header = ptr::null_mut();
static mut HEAP_START: usize = 0;
static mut HEAP_END: usize = 0;
static mut IN_ALLOC: bool = false;

fn heap_start_sym() -> usize {
    core::ptr::addr_of!(__heap_start) as usize
}

fn heap_end_sym() -> usize {
    core::ptr::addr_of!(__heap_end) as usize
}

fn align_up(x: usize, align: usize) -> Option<usize> {
    if align == 0 || !align.is_power_of_two() {
        return None;
    }
    let mask = align - 1;
    x.checked_add(mask).map(|s| s & !mask)
}

unsafe fn next(h: *mut Header) -> *mut Header {
    (*h).extra as *mut Header
}

unsafe fn set_next(h: *mut Header, n: *mut Header) {
    (*h).extra = n as usize;
}

fn in_heap(start: usize, size: usize) -> bool {
    let hs = unsafe { HEAP_START };
    let he = unsafe { HEAP_END };
    size >= MIN_FREE
        && start >= hs
        && start % HEADER_ALIGN == 0
        && start.checked_add(size).is_some_and(|end| end <= he)
}

/// Insert `[start, start+size)` into the address-sorted list, merging with
/// neighbours that share an edge. Panics on overlap (double-free / corruption).
unsafe fn insert_coalesced(start: usize, size: usize) {
    if !in_heap(start, size) {
        panic!("heap insert out of range: {:#x}+{:#x}", start, size);
    }
    let mut prev: *mut Header = ptr::null_mut();
    let mut cur = HEAD;
    while !cur.is_null() && (cur as usize) < start {
        let cend = (cur as usize) + (*cur).size;
        if start < cend {
            panic!(
                "heap overlap (double-free?): insert {:#x}+{:#x} hits {:#x}+{:#x}",
                start,
                size,
                cur as usize,
                (*cur).size
            );
        }
        prev = cur;
        cur = next(cur);
    }
    if !cur.is_null() && start + size > cur as usize {
        panic!(
            "heap overlap (double-free?): insert {:#x}+{:#x} hits {:#x}+{:#x}",
            start,
            size,
            cur as usize,
            (*cur).size
        );
    }

    let mut size = size;
    let mut nxt = cur;
    if !cur.is_null() && start + size == cur as usize {
        size += (*cur).size;
        nxt = next(cur);
    }
    if !prev.is_null() && (prev as usize) + (*prev).size == start {
        (*prev).size += size;
        set_next(prev, nxt);
        return;
    }
    let h = start as *mut Header;
    (*h).size = size;
    set_next(h, nxt);
    if prev.is_null() {
        HEAD = h;
    } else {
        set_next(prev, h);
    }
}

/// First-fit. Null if nothing fits or the request overflows. Never wraps to
/// a bogus pointer: every size add is `checked_*`.
unsafe fn try_alloc(layout: Layout) -> *mut u8 {
    let align = layout.align();
    let user_size = layout.size();
    if align == 0 || !align.is_power_of_two() {
        panic!("heap alloc: bad align {}", align);
    }

    let mut prev: *mut Header = ptr::null_mut();
    let mut cur = HEAD;
    while !cur.is_null() {
        let start = cur as usize;
        let block_size = (*cur).size;
        let end = start + block_size;
        let nxt = next(cur);

        let Some(user_base) = start.checked_add(HEADER) else {
            prev = cur;
            cur = nxt;
            continue;
        };
        let Some(user) = align_up(user_base, align) else {
            prev = cur;
            cur = nxt;
            continue;
        };
        let hdr = match user.checked_sub(HEADER) {
            Some(h) if h >= start => h,
            _ => {
                prev = cur;
                cur = nxt;
                continue;
            }
        };
        let Some(payload_end) = user.checked_add(user_size) else {
            return ptr::null_mut();
        };
        let Some(taken_end) = align_up(payload_end, HEADER_ALIGN) else {
            return ptr::null_mut();
        };
        if taken_end > end {
            prev = cur;
            cur = nxt;
            continue;
        }

        let prefix = hdr - start;
        let mut taken_end = taken_end;
        let suffix = end - taken_end;
        if suffix < MIN_FREE {
            taken_end = end;
        }
        let suffix = end - taken_end;

        if prefix >= MIN_FREE {
            (*cur).size = prefix;
            let ah = hdr as *mut Header;
            (*ah).size = taken_end - hdr;
            (*ah).extra = 0;
            if suffix >= MIN_FREE {
                let s = taken_end as *mut Header;
                (*s).size = suffix;
                set_next(s, nxt);
                set_next(cur, s);
            }
            return user as *mut u8;
        }

        // Absorb a too-small prefix into the allocated block.
        unlink(prev, nxt);
        let ah = hdr as *mut Header;
        (*ah).size = taken_end - start;
        (*ah).extra = prefix;
        if suffix >= MIN_FREE {
            let s = taken_end as *mut Header;
            (*s).size = suffix;
            set_next(s, nxt);
            if prev.is_null() {
                HEAD = s;
            } else {
                set_next(prev, s);
            }
        }
        return user as *mut u8;
    }
    ptr::null_mut()
}

unsafe fn unlink(prev: *mut Header, nxt: *mut Header) {
    if prev.is_null() {
        HEAD = nxt;
    } else {
        set_next(prev, nxt);
    }
}

unsafe fn dealloc_raw(ptr: *mut u8, _layout: Layout) {
    if ptr.is_null() {
        panic!("heap dealloc of null");
    }
    let user = ptr as usize;
    let hdr = user
        .checked_sub(HEADER)
        .expect("heap dealloc: ptr underflows") as *mut Header;
    if (hdr as usize) % HEADER_ALIGN != 0 {
        panic!("heap dealloc: header {:#x} unaligned", hdr as usize);
    }
    let pad = (*hdr).extra;
    let start = (hdr as usize)
        .checked_sub(pad)
        .expect("heap dealloc: pad underflows");
    let size = (*hdr).size;
    insert_coalesced(start, size);
}

/// `(free_blocks, free_bytes)`. Read-only walk of the coalesced list.
#[cfg(feature = "stress")]
pub fn free_stats() -> (usize, usize) {
    let mut n = 0usize;
    let mut bytes = 0usize;
    let mut cur = unsafe { HEAD };
    while !cur.is_null() {
        bytes += unsafe { (*cur).size };
        n += 1;
        if n > 1024 {
            panic!("heap free list looks cyclic");
        }
        cur = unsafe { next(cur) };
    }
    (n, bytes)
}

#[cfg(feature = "stress")]
pub fn region_size() -> usize {
    unsafe { HEAP_END - HEAP_START }
}

#[cfg(not(feature = "fast-boot"))]
fn dump(tag: &str) {
    let mut n = 0usize;
    let mut bytes = 0usize;
    let mut cur = unsafe { HEAD };
    println!("heap {}:", tag);
    while !cur.is_null() {
        let size = unsafe { (*cur).size };
        println!("  free[{}] pa={:#x} size={}", n, cur as usize, size);
        bytes += size;
        n += 1;
        if n > 1024 {
            panic!("heap free list looks cyclic");
        }
        cur = unsafe { next(cur) };
    }
    println!("  blocks={} bytes={}", n, bytes);
}

pub fn init() {
    let start = heap_start_sym();
    let end = heap_end_sym();
    if start % HEADER_ALIGN != 0 || end <= start || (end - start) < MIN_FREE {
        panic!("heap symbols unusable: start={:#x} end={:#x}", start, end);
    }
    unsafe {
        if HEAP_START != 0 {
            panic!("heap::init called twice");
        }
        HEAP_START = start;
        HEAP_END = end;
        let h = start as *mut Header;
        (*h).size = end - start;
        set_next(h, ptr::null_mut());
        HEAD = h;
    }
}

unsafe impl GlobalAlloc for Heap {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        if unsafe { HEAP_START } == 0 {
            panic!(
                "heap alloc before init: size={} align={}",
                layout.size(),
                layout.align()
            );
        }
        if unsafe { IN_ALLOC } {
            panic!(
                "heap re-entered: size={} align={}",
                layout.size(),
                layout.align()
            );
        }
        unsafe { IN_ALLOC = true };
        let p = unsafe { try_alloc(layout) };
        unsafe { IN_ALLOC = false };
        if p.is_null() {
            panic!(
                "heap exhausted: size={} align={}",
                layout.size(),
                layout.align()
            );
        }
        p
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        if unsafe { IN_ALLOC } {
            panic!(
                "heap dealloc re-entered: size={} align={}",
                layout.size(),
                layout.align()
            );
        }
        unsafe { IN_ALLOC = true };
        unsafe { dealloc_raw(ptr, layout) };
        unsafe { IN_ALLOC = false };
    }
}

#[cfg(not(feature = "fast-boot"))]
#[repr(align(64))]
struct Align64(u64);

/// `Box::new(42)`, a `Vec` grown to 10_000, a `String`, drop, allocate again.
/// Prints the free list after the Vec churn (the interesting snapshot) and
/// `HEAP OK`.
#[cfg(not(feature = "fast-boot"))]
pub fn self_test() {
    use alloc::boxed::Box;
    use alloc::string::String;
    use alloc::vec::Vec;
    dump("init");

    let b = Box::new(42i32);
    if *b != 42 {
        panic!("Box round-trip {}", *b);
    }

    let mut v: Vec<i32> = Vec::new();
    for i in 0..10_000 {
        v.push(i);
    }
    if v.len() != 10_000 || v[0] != 0 || v[9999] != 9999 {
        panic!("Vec contents wrong len={}", v.len());
    }
    println!("vec len={} cap={}", v.len(), v.capacity());
    dump("after vec");

    let a = Box::new(Align64(0x55aa));
    let ap = a.as_ref() as *const Align64 as usize;
    if ap % 64 != 0 || a.0 != 0x55aa {
        panic!("over-aligned Box at {:#x} val={:#x}", ap, a.0);
    }
    println!("align64 user={:#x}", ap);
    dump("after align64");

    let s = String::from("whimbrel");
    if s != "whimbrel" {
        panic!("String round-trip");
    }

    drop(a);
    drop(s);
    drop(v);
    drop(b);

    let again = Box::new(7i32);
    if *again != 7 {
        panic!("realloc after drop {}", *again);
    }
    dump("after drop+realloc");

    // Exhaustion must not wrap into a pointer. 2 MiB cannot fit in 1 MiB.
    let huge = Layout::from_size_align(2 * 1024 * 1024, 1).unwrap();
    let p = unsafe { try_alloc(huge) };
    if !p.is_null() {
        panic!("2 MiB alloc succeeded on 1 MiB heap: {:#x}", p as usize);
    }

    println!("HEAP OK");
}
