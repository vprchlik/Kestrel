//! Allocator storm: interleaved frame/heap alloc/free under live ticks.
//!
//! Does not change the allocators. It holds a small working set, mutates it
//! with an LCG for several thousand steps, then asserts the heap is one
//! coalesced block and available frames (`free_count`: bump remainder plus
//! recycled) match the start. A recycled-list walk checks `RECYCLED`
//! (finding 30 / D-0065). A second pass runs at 1 ms ticks to widen the
//! window where a timer can land inside `alloc_frame` / `try_alloc`.

use crate::frame;
use crate::heap;
use crate::println;
use crate::timer;
use alloc::alloc::{alloc, dealloc};
use core::alloc::Layout;

const ITERS: usize = 4096;
const SLOTS: usize = 16;
const LCG_SEED: u32 = 0xC0FFEE;

struct Lcg(u32);

impl Lcg {
    fn next(&mut self) -> u32 {
        // Numerical Recipes LCG.
        self.0 = self.0.wrapping_mul(1664525).wrapping_add(1013904223);
        self.0
    }
}

struct HeapSlot {
    ptr: *mut u8,
    layout: Layout,
}

fn assert_restored(frames_start: usize, tag: &str) {
    frame::check_recycled();
    let frames = frame::free_count();
    if frames != frames_start {
        panic!(
            "storm {}: frames free {} want {}",
            tag, frames, frames_start
        );
    }
    let (blocks, bytes) = heap::free_stats();
    let region = heap::region_size();
    if blocks != 1 || bytes != region {
        panic!(
            "storm {}: heap blocks={} bytes={} want 1 / {}",
            tag, blocks, bytes, region
        );
    }
}

fn storm(rng: &mut Lcg, tag: &str) {
    let frames_start = frame::free_count();
    let (blocks0, bytes0) = heap::free_stats();
    if blocks0 != 1 || bytes0 != heap::region_size() {
        panic!(
            "storm {} start: heap not one block (blocks={} bytes={})",
            tag, blocks0, bytes0
        );
    }

    let mut frames = [0usize; SLOTS];
    let mut heaps: [HeapSlot; SLOTS] = core::array::from_fn(|_| HeapSlot {
        ptr: core::ptr::null_mut(),
        layout: Layout::from_size_align(1, 1).unwrap(),
    });

    for _ in 0..ITERS {
        let r = rng.next();
        let slot = (r as usize) % SLOTS;
        if r & 1 == 0 {
            if frames[slot] == 0 {
                frames[slot] = frame::alloc_frame();
            } else {
                frame::free_frame(frames[slot]);
                frames[slot] = 0;
            }
        } else {
            if heaps[slot].ptr.is_null() {
                let size = 8usize << ((r >> 8) & 7); // 8..1024
                let align = 1usize << ((r >> 16) & 3); // 1, 2, 4, 8
                let layout = Layout::from_size_align(size, align)
                    .unwrap_or_else(|_| panic!("layout size={} align={}", size, align));
                let ptr = unsafe { alloc(layout) };
                heaps[slot] = HeapSlot { ptr, layout };
            } else {
                unsafe { dealloc(heaps[slot].ptr, heaps[slot].layout) };
                heaps[slot].ptr = core::ptr::null_mut();
            }
        }
    }

    for slot in 0..SLOTS {
        if frames[slot] != 0 {
            frame::free_frame(frames[slot]);
            frames[slot] = 0;
        }
        if !heaps[slot].ptr.is_null() {
            unsafe { dealloc(heaps[slot].ptr, heaps[slot].layout) };
            heaps[slot].ptr = core::ptr::null_mut();
        }
    }

    assert_restored(frames_start, tag);
    println!(
        "storm {} iters={} frames_free={} heap_bytes={}",
        tag,
        ITERS,
        frame::free_count(),
        heap::region_size()
    );
}

pub fn run() {
    let mut rng = Lcg(LCG_SEED);
    let ticks0 = timer::ticks();
    storm(&mut rng, "10ms");
    println!("STORM OK");

    timer::set_period(timer::PERIOD_1MS);
    let ticks1 = timer::ticks();
    storm(&mut rng, "1ms");
    let ticks2 = timer::ticks();
    timer::set_period(timer::PERIOD);
    println!("storm 1ms ticks {} -> {} (period 10000)", ticks1, ticks2);
    println!("STORM 1MS OK");
    println!("stress ticks at start {}", ticks0);
}
