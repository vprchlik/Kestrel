//! Per-task layout symbols and the `MAX_TASKS` count.
//!
//! Owns the compile-time task-slot count and the linker symbols for user
//! sections, per-task stacks, guard holes, and break windows (D-0030,
//! D-0031). T2.4 puts the TCB on top of this. Without the count assert, the
//! linker script and Rust can drift and a later `sscratch` would point at
//! the wrong stack.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::frame::PAGE_SIZE;
use crate::println;

/// Compile-time task slots. Demo uses 2. D-0030.
pub const MAX_TASKS: usize = 4;
/// One slot: 8 KiB user stack + 8 KiB kernel stack + 64 KiB break + two
/// 4 KiB guards. D-0030.
pub const TASK_RESERVE: usize = 88 * 1024;
const STACK: usize = 8 * 1024;
const BREAK: usize = 64 * 1024;
const GUARD: usize = 4 * 1024;

const _: () = assert!(TASK_RESERVE == STACK + BREAK + STACK + GUARD + GUARD);
const _: () = assert!(MAX_TASKS * TASK_RESERVE / PAGE_SIZE == 88);

extern "C" {
    static __utext_start: u8;
    static __utext_end: u8;
    static __urodata_start: u8;
    static __urodata_end: u8;
    static __udata_start: u8;
    static __udata_end: u8;
    static __ubss_start: u8;
    static __ubss_end: u8;
    static __tasks_start: u8;
    static __tasks_end: u8;
    static __ustack0_guard: u8;
    static __ustack0_bottom: u8;
    static __ustack0_top: u8;
    static __ubrk0_base: u8;
    static __ubrk0_wall: u8;
    static __kstack0_guard: u8;
    static __kstack0_bottom: u8;
    static __kstack0_top: u8;
    static __ustack1_guard: u8;
    static __ustack1_bottom: u8;
    static __ustack1_top: u8;
    static __ubrk1_base: u8;
    static __ubrk1_wall: u8;
    static __kstack1_guard: u8;
    static __kstack1_bottom: u8;
    static __kstack1_top: u8;
    static __ustack2_guard: u8;
    static __ustack2_bottom: u8;
    static __ustack2_top: u8;
    static __ubrk2_base: u8;
    static __ubrk2_wall: u8;
    static __kstack2_guard: u8;
    static __kstack2_bottom: u8;
    static __kstack2_top: u8;
    static __ustack3_guard: u8;
    static __ustack3_bottom: u8;
    static __ustack3_top: u8;
    static __ubrk3_base: u8;
    static __ubrk3_wall: u8;
    static __kstack3_guard: u8;
    static __kstack3_bottom: u8;
    static __kstack3_top: u8;
    static __kernel_end: u8;
    static __heap_start: u8;
}

fn pa(sym: *const u8) -> usize {
    sym as usize
}

/// One task slot's linker addresses. All 4 KiB-aligned. D-0030.
#[derive(Clone, Copy)]
pub struct Slot {
    pub ustack_guard: usize,
    pub ustack_bottom: usize,
    pub ustack_top: usize,
    pub brk_base: usize,
    pub brk_wall: usize,
    pub kstack_guard: usize,
    pub kstack_bottom: usize,
    pub kstack_top: usize,
}

pub fn utext() -> (usize, usize) {
    (
        pa(core::ptr::addr_of!(__utext_start)),
        pa(core::ptr::addr_of!(__utext_end)),
    )
}
pub fn urodata() -> (usize, usize) {
    (
        pa(core::ptr::addr_of!(__urodata_start)),
        pa(core::ptr::addr_of!(__urodata_end)),
    )
}
pub fn udata() -> (usize, usize) {
    (
        pa(core::ptr::addr_of!(__udata_start)),
        pa(core::ptr::addr_of!(__udata_end)),
    )
}
pub fn ubss() -> (usize, usize) {
    (
        pa(core::ptr::addr_of!(__ubss_start)),
        pa(core::ptr::addr_of!(__ubss_end)),
    )
}
pub fn tasks_span() -> (usize, usize) {
    (
        pa(core::ptr::addr_of!(__tasks_start)),
        pa(core::ptr::addr_of!(__tasks_end)),
    )
}

/// Panic if `id >= MAX_TASKS`.
pub fn slot(id: usize) -> Slot {
    match id {
        0 => Slot {
            ustack_guard: pa(core::ptr::addr_of!(__ustack0_guard)),
            ustack_bottom: pa(core::ptr::addr_of!(__ustack0_bottom)),
            ustack_top: pa(core::ptr::addr_of!(__ustack0_top)),
            brk_base: pa(core::ptr::addr_of!(__ubrk0_base)),
            brk_wall: pa(core::ptr::addr_of!(__ubrk0_wall)),
            kstack_guard: pa(core::ptr::addr_of!(__kstack0_guard)),
            kstack_bottom: pa(core::ptr::addr_of!(__kstack0_bottom)),
            kstack_top: pa(core::ptr::addr_of!(__kstack0_top)),
        },
        1 => Slot {
            ustack_guard: pa(core::ptr::addr_of!(__ustack1_guard)),
            ustack_bottom: pa(core::ptr::addr_of!(__ustack1_bottom)),
            ustack_top: pa(core::ptr::addr_of!(__ustack1_top)),
            brk_base: pa(core::ptr::addr_of!(__ubrk1_base)),
            brk_wall: pa(core::ptr::addr_of!(__ubrk1_wall)),
            kstack_guard: pa(core::ptr::addr_of!(__kstack1_guard)),
            kstack_bottom: pa(core::ptr::addr_of!(__kstack1_bottom)),
            kstack_top: pa(core::ptr::addr_of!(__kstack1_top)),
        },
        2 => Slot {
            ustack_guard: pa(core::ptr::addr_of!(__ustack2_guard)),
            ustack_bottom: pa(core::ptr::addr_of!(__ustack2_bottom)),
            ustack_top: pa(core::ptr::addr_of!(__ustack2_top)),
            brk_base: pa(core::ptr::addr_of!(__ubrk2_base)),
            brk_wall: pa(core::ptr::addr_of!(__ubrk2_wall)),
            kstack_guard: pa(core::ptr::addr_of!(__kstack2_guard)),
            kstack_bottom: pa(core::ptr::addr_of!(__kstack2_bottom)),
            kstack_top: pa(core::ptr::addr_of!(__kstack2_top)),
        },
        3 => Slot {
            ustack_guard: pa(core::ptr::addr_of!(__ustack3_guard)),
            ustack_bottom: pa(core::ptr::addr_of!(__ustack3_bottom)),
            ustack_top: pa(core::ptr::addr_of!(__ustack3_top)),
            brk_base: pa(core::ptr::addr_of!(__ubrk3_base)),
            brk_wall: pa(core::ptr::addr_of!(__ubrk3_wall)),
            kstack_guard: pa(core::ptr::addr_of!(__kstack3_guard)),
            kstack_bottom: pa(core::ptr::addr_of!(__kstack3_bottom)),
            kstack_top: pa(core::ptr::addr_of!(__kstack3_top)),
        },
        _ => panic!("task slot {} >= MAX_TASKS {}", id, MAX_TASKS),
    }
}

fn require(name: &str, got: usize, want: usize) {
    if got != want {
        panic!("{}: {:#x} want {:#x}", name, got, want);
    }
}

fn require_aligned(name: &str, addr: usize) {
    if addr % PAGE_SIZE != 0 {
        panic!("{}: {:#x} is not 4 KiB-aligned", name, addr);
    }
}

fn check_slot(
    id: usize,
    uguard: usize,
    ubottom: usize,
    utop: usize,
    brk_base: usize,
    brk_wall: usize,
    kguard: usize,
    kbottom: usize,
    ktop: usize,
) {
    require_aligned("ustack guard", uguard);
    require(
        "ustack guard size",
        ubottom.wrapping_sub(uguard),
        GUARD,
    );
    require("ustack size", utop.wrapping_sub(ubottom), STACK);
    require("break size", brk_wall.wrapping_sub(brk_base), BREAK);
    require("kstack guard size", kbottom.wrapping_sub(kguard), GUARD);
    require("kstack size", ktop.wrapping_sub(kbottom), STACK);
    // Stacks sit on their guards; break is adjacent to the user stack
    // (both will be U+R+W — no guard required). Kernel stack sits on
    // its own guard, which is the hole below it.
    if ubottom != uguard + GUARD
        || utop != ubottom + STACK
        || brk_base != utop
        || brk_wall != brk_base + BREAK
        || kguard != brk_wall
        || kbottom != kguard + GUARD
        || ktop != kbottom + STACK
    {
        panic!(
            "task {} layout: ug={:#x} ub={:#x} ut={:#x} bb={:#x} bw={:#x} kg={:#x} kb={:#x} kt={:#x}",
            id, uguard, ubottom, utop, brk_base, brk_wall, kguard, kbottom, ktop
        );
    }
}

/// Panic unless the linker layout matches `MAX_TASKS` and D-0030 sizes.
/// The linker count is `__tasks_end - __tasks_start` / `TASK_RESERVE`;
/// a `const` assert cannot see linker symbols, so this is the mirror.
pub fn check_layout() {
    let utext_s = pa(core::ptr::addr_of!(__utext_start));
    let utext_e = pa(core::ptr::addr_of!(__utext_end));
    let urod_s = pa(core::ptr::addr_of!(__urodata_start));
    let urod_e = pa(core::ptr::addr_of!(__urodata_end));
    let udat_s = pa(core::ptr::addr_of!(__udata_start));
    let udat_e = pa(core::ptr::addr_of!(__udata_end));
    let ubss_s = pa(core::ptr::addr_of!(__ubss_start));
    let ubss_e = pa(core::ptr::addr_of!(__ubss_end));
    let tasks_s = pa(core::ptr::addr_of!(__tasks_start));
    let tasks_e = pa(core::ptr::addr_of!(__tasks_end));
    let kend = pa(core::ptr::addr_of!(__kernel_end));
    let hstart = pa(core::ptr::addr_of!(__heap_start));

    require_aligned(".utext", utext_s);
    require_aligned(".urodata", urod_s);
    require_aligned(".udata", udat_s);
    require_aligned(".ubss", ubss_s);
    require_aligned("__tasks_start", tasks_s);
    if !(utext_s <= utext_e
        && utext_e <= urod_s
        && urod_s <= urod_e
        && urod_e <= udat_s
        && udat_s <= udat_e
        && udat_e <= ubss_s
        && ubss_s <= ubss_e
        && ubss_e <= tasks_s
        && tasks_s < tasks_e
        && tasks_e <= kend
        && kend == hstart)
    {
        panic!(
            "user/task symbol order: utext={:#x}..{:#x} urod={:#x}..{:#x} udat={:#x}..{:#x} ubss={:#x}..{:#x} tasks={:#x}..{:#x} kend={:#x} heap={:#x}",
            utext_s, utext_e, urod_s, urod_e, udat_s, udat_e, ubss_s, ubss_e, tasks_s, tasks_e, kend, hstart
        );
    }

    let reserve = tasks_e - tasks_s;
    if reserve != MAX_TASKS * TASK_RESERVE {
        panic!(
            "task reserve {} bytes, want {} (MAX_TASKS={} × {})",
            reserve,
            MAX_TASKS * TASK_RESERVE,
            MAX_TASKS,
            TASK_RESERVE
        );
    }

    check_slot(
        0,
        slot(0).ustack_guard,
        slot(0).ustack_bottom,
        slot(0).ustack_top,
        slot(0).brk_base,
        slot(0).brk_wall,
        slot(0).kstack_guard,
        slot(0).kstack_bottom,
        slot(0).kstack_top,
    );
    check_slot(
        1,
        slot(1).ustack_guard,
        slot(1).ustack_bottom,
        slot(1).ustack_top,
        slot(1).brk_base,
        slot(1).brk_wall,
        slot(1).kstack_guard,
        slot(1).kstack_bottom,
        slot(1).kstack_top,
    );
    check_slot(
        2,
        slot(2).ustack_guard,
        slot(2).ustack_bottom,
        slot(2).ustack_top,
        slot(2).brk_base,
        slot(2).brk_wall,
        slot(2).kstack_guard,
        slot(2).kstack_bottom,
        slot(2).kstack_top,
    );
    check_slot(
        3,
        slot(3).ustack_guard,
        slot(3).ustack_bottom,
        slot(3).ustack_top,
        slot(3).brk_base,
        slot(3).brk_wall,
        slot(3).kstack_guard,
        slot(3).kstack_bottom,
        slot(3).kstack_top,
    );

    println!(
        "tasks {} reserve={}KiB each utext={:#x}..{:#x} tasks={:#x}..{:#x}",
        MAX_TASKS,
        TASK_RESERVE / 1024,
        utext_s,
        utext_e,
        tasks_s,
        tasks_e
    );
}
