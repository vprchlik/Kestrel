//! Per-task layout and the static task table.
//!
//! Owns the compile-time task-slot count, the linker symbols for user
//! sections / stacks / guards / break windows (D-0030, D-0031), and the
//! TCB array (D-0032). The TCB stores a pointer to the trap frame at
//! `kstack_top - 272`, not an inline copy — the frame *is* the task
//! context. `create` writes that frame onto a linker-reserved kernel
//! stack and touches neither the frame allocator nor the heap (D-0036).
//! Without the count assert, the linker script and Rust can drift and a
//! later `sscratch` would point at the wrong stack.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::csr;
use crate::frame::PAGE_SIZE;
use crate::println;
use crate::trap::{self, TrapFrame};

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
    // Distinct `extern static`s are non-aliasing to LLVM, so `==` on
    // their addresses constant-folds to false under LTO even when the
    // linker placed them at the same VA (`__kernel_end` == `__heap_start`).
    // `black_box` keeps the comparison as a runtime load of the symbol.
    core::hint::black_box(sym as usize)
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
    require("ustack guard size", ubottom.wrapping_sub(uguard), GUARD);
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

// ---------------------------------------------------------------------------
// TCB. The trap frame lives on the task's kernel stack, not in this struct
// (D-0032). `frame` is `kstack_top - FRAME_SIZE`.
// ---------------------------------------------------------------------------

/// RISC-V `x2` / `sp`.
const REG_SP: usize = 2;
/// RISC-V `x3` / `gp`.
const REG_GP: usize = 3;
/// RISC-V `x4` / `tp`.
const REG_TP: usize = 4;

/// Fabricated `sstatus` for a new task (D-0032).
///
/// - `SPP` = 0 → `sret` enters U-mode
/// - `SPIE` = 1 → `sret` sets `SIE` from `SPIE`, so SIE becomes 1 in U
/// - `SIE` = 0 → the `csrw sstatus` in `__trap_return` must not enable
///   interrupts in S-mode in the window before `sret`
/// - `FS` = Off (0) → an FP instruction from U is an undelegated illegal
///   instruction (OpenSBI dump), not a silent FP op. Boot `sstatus` has
///   FS=Dirty (`0x8000000200006000`); we do not copy it.
/// - `UXL` = 64, matching OpenSBI. Clearing it would change UXLEN.
///
/// Value: `SPIE | UXL_64` = `0x20 | (2 << 32)` = `0x200000020`.
const FABRICATED_SSTATUS: usize = csr::sstatus::SPIE | csr::sstatus::UXL_64;

const _: () = assert!(FABRICATED_SSTATUS & csr::sstatus::SIE == 0);
const _: () = assert!(FABRICATED_SSTATUS & csr::sstatus::SPP == 0);
const _: () = assert!(FABRICATED_SSTATUS & csr::sstatus::SPIE != 0);
const _: () = assert!(FABRICATED_SSTATUS & csr::sstatus::FS == 0);
const _: () = assert!(FABRICATED_SSTATUS & csr::sstatus::SUM == 0);
const _: () = assert!((FABRICATED_SSTATUS & csr::sstatus::UXL) == csr::sstatus::UXL_64);

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum State {
    Ready,
    Running,
    Exited,
}

/// Task control block. Register state lives in `*frame`, not here.
#[derive(Clone, Copy)]
pub struct Task {
    pub id: usize,
    pub state: State,
    pub frame: *mut TrapFrame,
    pub kstack_top: usize,
    pub ustack_top: usize,
    pub brk_base: usize,
    pub brk: usize,
    pub brk_wall: usize,
    pub exit_code: usize,
    pub writes: usize,
    pub yields: usize,
}

static mut TASKS: [Option<Task>; MAX_TASKS] = [None; MAX_TASKS];
/// Running task id, or `None` in S-mode with no user task (boot, after kill).
static mut CURRENT: Option<usize> = None;

fn state_name(s: State) -> &'static str {
    match s {
        State::Ready => "Ready",
        State::Running => "Running",
        State::Exited => "Exited",
    }
}

/// Panic unless `id` names a created task.
pub fn get(id: usize) -> &'static mut Task {
    if id >= MAX_TASKS {
        panic!("task {} >= MAX_TASKS {}", id, MAX_TASKS);
    }
    match unsafe { TASKS[id].as_mut() } {
        Some(t) => t,
        None => panic!("task {} not created", id),
    }
}

/// Fabricate a trap frame at `kstack_top - 272` and record the TCB.
///
/// No allocation (D-0036): writes a `static` slot and `ptr::write`s 272
/// bytes onto a linker-reserved kernel stack that has never been used
/// (boot runs on `__boot_stack`). This module does not import `alloc` or
/// `frame`; a later `Box`/`Vec` would fail to compile without adding
/// `alloc::`, a heap alloc before `heap::init` panics `heap alloc before
/// init`, and `frame::alloc_frame` would be an explicit call (T2.11's
/// `freeze()` will panic if create ever runs after the freeze).
pub fn create(id: usize, entry: usize) {
    if id >= MAX_TASKS {
        panic!("create: id {} >= MAX_TASKS {}", id, MAX_TASKS);
    }
    if unsafe { TASKS[id].is_some() } {
        panic!("create: task {} already exists", id);
    }

    let s = slot(id);
    let frame_pa = s.kstack_top - trap::FRAME_SIZE;
    if frame_pa < s.kstack_bottom {
        panic!(
            "create: task {} frame {:#x} below kstack {:#x}..{:#x}",
            id, frame_pa, s.kstack_bottom, s.kstack_top
        );
    }

    // Virgin kernel stack: NOLOAD, never executed on. First store here.
    let mut x = [0usize; 32];
    x[REG_SP] = s.ustack_top;
    // REG_GP and REG_TP stay 0 (D-0032): a gp-/tp-relative user access
    // faults near 0 instead of reading kernel data.
    debug_assert!(x[REG_GP] == 0 && x[REG_TP] == 0);

    let frame = frame_pa as *mut TrapFrame;
    unsafe {
        core::ptr::write(
            frame,
            TrapFrame {
                x,
                sepc: entry,
                sstatus: FABRICATED_SSTATUS,
            },
        );
        TASKS[id] = Some(Task {
            id,
            state: State::Ready,
            frame,
            kstack_top: s.kstack_top,
            ustack_top: s.ustack_top,
            brk_base: s.brk_base,
            brk: s.brk_base,
            brk_wall: s.brk_wall,
            exit_code: 0,
            writes: 0,
            yields: 0,
        });
    }
}

/// Create the static table. Default (and HTTP/UDP/persist/fast-boot)
/// images run the compiled app in slot 3 (D-0051). Userptr selftests
/// keep a single Ready task in slot 0. `user-fault-selftest` keeps the
/// T2.10 two-task table; `kmain` `enter`s task 2 so the load page fault
/// is the first U-mode work.
pub fn init() {
    #[cfg(any(feature = "userptr-kernel-selftest", feature = "userptr-span-selftest"))]
    {
        create(0, crate::user::entry());
        for id in 1..MAX_TASKS {
            create(id, 0);
            get(id).state = State::Exited;
        }
    }
    #[cfg(feature = "user-fault-selftest")]
    {
        create(0, 0);
        create(1, crate::user::task1());
        create(2, crate::user::task2());
        create(3, 0);
        get(0).state = State::Exited;
        get(3).state = State::Exited;
    }
    #[cfg(not(any(
        feature = "userptr-kernel-selftest",
        feature = "userptr-span-selftest",
        feature = "user-fault-selftest",
    )))]
    {
        create(0, 0);
        create(1, 0);
        create(2, 0);
        create(3, crate::user::app());
        get(0).state = State::Exited;
        get(1).state = State::Exited;
        get(2).state = State::Exited;
    }
    println!(
        "fabricated sstatus {:#x} (SPIE=1 SPP=U SIE=0 FS=Off UXL=64)",
        FABRICATED_SSTATUS
    );
    for id in 0..MAX_TASKS {
        let t = get(id);
        let frame = t.frame as usize;
        if frame != t.kstack_top - trap::FRAME_SIZE {
            panic!(
                "task {} frame {:#x} != kstack_top {:#x} - {}",
                t.id,
                frame,
                t.kstack_top,
                trap::FRAME_SIZE
            );
        }
        let sepc = unsafe { (*t.frame).sepc };
        println!(
            "task {} {} frame={:#x} sepc={:#x} kstack_top={:#x} ustack_top={:#x} brk={:#x}..{:#x} (at {:#x}) exit={}",
            t.id,
            state_name(t.state),
            frame,
            sepc,
            t.kstack_top,
            t.ustack_top,
            t.brk_base,
            t.brk_wall,
            t.brk,
            t.exit_code
        );
    }
}

/// Mark `id` Running and `sret` into its fabricated frame. Does not return
/// (D-0035). `sscratch` is still 0 here; `__trap_return`'s U tail parks the
/// user `sp` and swaps `kstack_top` into `sscratch` immediately before `sret`.
#[cfg(not(feature = "no-sret"))]
pub fn enter(id: usize) -> ! {
    // D-0036: freeze before the first `sret`. Two independent reasons the
    // D-0028 hazard is gone (they fail independently, which is why both
    // are recorded):
    //
    // 1. Nothing in the trap path allocates. Kernel stacks and user
    //    sections are linker-placed (D-0030, D-0031); the map is complete
    //    and never edited (D-0031); `sbrk` moves a pointer inside a
    //    preallocated window.
    // 2. After `kmain` stops returning, no kernel code runs with `SIE=1`.
    //    The scheduler does not change that: `preempt` / `yield_cpu` /
    //    `after_exit` only return a frame pointer. Hardware cleared `SIE`
    //    on trap; we never set it in the handler (D-0020). `sret` restores
    //    `SIE` from `SPIE` in U only. The handler is the only kernel code
    //    that runs at all.
    //
    // Consumed frames are 67 tables (M2's 65 plus D-0039's L1+L0 for
    // the virtio-mmio VPN[2]) plus, except under `fast-boot`, the two
    // FRAME OK self-test leftovers (D-0036). The MMIO pages themselves
    // are not RAM and do not come from the pool — `total_frames()` is
    // unchanged. Feature images can shift `total` with `__heap_end`;
    // the split must still hold.
    {
        let total = crate::frame::total_frames();
        let free = crate::frame::free_count();
        let tables = crate::page::tables_used();
        let held = total - free;
        let leftover = if cfg!(feature = "fast-boot") { 0 } else { 2 };
        if tables != 67 || held != tables + leftover {
            panic!(
                "frames held {} tables {} leftover {} want tables=67 leftover={}",
                held, tables, leftover, leftover
            );
        }
        println!(
            "frames consumed: tables={} selftest={} held={}",
            tables, leftover, held
        );
    }
    crate::frame::freeze();
    crate::phase::stamp(crate::phase::FREEZE);
    let t = get(id);
    let sepc = unsafe { (*t.frame).sepc };
    println!(
        "enter task {} sepc={:#x} frame={:#x} kstack_top={:#x}",
        t.id, sepc, t.frame as usize, t.kstack_top
    );
    t.state = State::Running;
    unsafe { CURRENT = Some(id) };
    crate::phase::stamp(crate::phase::SRET);
    trap::resume(t.frame);
}

/// The task `enter` last `sret`'d into. Panics if called with none running.
pub fn current() -> &'static mut Task {
    match unsafe { CURRENT } {
        Some(id) => get(id),
        None => panic!("no current task"),
    }
}

pub fn has_current() -> bool {
    unsafe { core::ptr::read_volatile(&raw const CURRENT) }.is_some()
}

#[allow(dead_code)]
static mut SWITCH_12: usize = 0;
#[allow(dead_code)]
static mut SWITCH_21: usize = 0;

fn next_ready_after(id: usize) -> Option<usize> {
    for step in 1..=MAX_TASKS {
        let cand = (id + step) % MAX_TASKS;
        if get(cand).state == State::Ready {
            return Some(cand);
        }
    }
    None
}

fn record_switch(from: usize, to: usize) {
    if from == to {
        return;
    }
    unsafe {
        if from == 1 && to == 2 {
            SWITCH_12 = SWITCH_12.wrapping_add(1);
        } else if from == 2 && to == 1 {
            SWITCH_21 = SWITCH_21.wrapping_add(1);
        }
    }
}

fn run(id: usize) -> &'static mut TrapFrame {
    let t = get(id);
    t.state = State::Running;
    unsafe { CURRENT = Some(id) };
    unsafe { &mut *t.frame }
}

/// End the slice (D-0035): current stays Ready, return the next Ready
/// frame. One Ready task gets itself back. Policy lives here; the
/// assembly only does `mv sp, a0` (D-0032).
pub fn preempt(frame: &mut TrapFrame) -> &mut TrapFrame {
    let cur = current();
    if cur.frame as usize != frame as *mut TrapFrame as usize {
        panic!(
            "preempt: frame {:#x} is not task {}'s {:#x}",
            frame as *mut TrapFrame as usize, cur.id, cur.frame as usize
        );
    }
    cur.state = State::Ready;
    let next = next_ready_after(cur.id).unwrap_or(cur.id);
    record_switch(cur.id, next);
    run(next)
}

/// `yield`: same pick as a tick. Caller has already advanced `sepc`.
pub fn yield_cpu(frame: &mut TrapFrame) -> &mut TrapFrame {
    current().yields += 1;
    preempt(frame)
}

/// Last `exit` with an empty ready set: dump the stack and shut down.
/// HTTP/UDP images print their marker here. The user-fault selftest has
/// a different last-task path (D-0034). No idle loop (D-0035).
fn finish_sched() -> ! {
    #[cfg(feature = "user-fault-selftest")]
    {
        finish_user_fault();
    }
    #[cfg(not(feature = "user-fault-selftest"))]
    {
        finish_app();
    }
}

#[cfg(not(feature = "user-fault-selftest"))]
fn finish_app() -> ! {
    crate::net::dump();
    #[cfg(feature = "net-udp-selftest")]
    println!("NET UDP OK");
    #[cfg(feature = "tcp-drop-first-tx")]
    {
        if crate::tcp::rexmit() != 1 {
            panic!(
                "tcp: drop-first-tx expected exactly one retransmit, got {}",
                crate::tcp::rexmit()
            );
        }
        println!("HTTP RETRANSMIT OK");
    }
    #[cfg(all(not(feature = "net-udp-selftest"), not(feature = "tcp-drop-first-tx")))]
    println!("HTTP OK");
    stop_until_scheduler();
}

#[cfg(feature = "user-fault-selftest")]
fn finish_user_fault() -> ! {
    let t1 = get(1);
    let t2 = get(2);
    if t1.state != State::Exited || t2.state != State::Exited {
        panic!(
            "userfault: task1 {} task2 {}",
            state_name(t1.state),
            state_name(t2.state)
        );
    }
    if t1.writes == 0 {
        panic!("userfault: survivor wrote nothing");
    }
    println!("task 1 done writes={} yields={}", t1.writes, t1.yields);
    println!("USERFAULT OK");
    stop_until_scheduler();
}

/// Pick the next Ready after an exit. Empty ready set → `finish_sched`.
pub fn after_exit(exited_id: usize) -> &'static mut TrapFrame {
    match next_ready_after(exited_id) {
        Some(id) => {
            record_switch(exited_id, id);
            run(id)
        }
        None => finish_sched(),
    }
}

/// Mark the current task `Exited` and drop `CURRENT`. Does not resume anyone.
fn mark_exited() -> usize {
    let t = current();
    let id = t.id;
    t.state = State::Exited;
    unsafe { CURRENT = None };
    id
}

/// Kill the running task from a U-mode fault and return the next Ready
/// frame (D-0034).
///
/// We are on this task's kernel stack when we decide to kill it: `__trap_entry`
/// pushed the frame at `kstack_top - 272` and `trap_handler` is a Rust call
/// on that stack. `after_exit` returns a *different* slot's `frame` pointer
/// (`kstack_top - 272` of a still-Ready task). The handler returns normally,
/// so Rust pops its frames on the **dead** kstack — that is safe because the
/// slot is `Exited` and `next_ready_after` will never pick it. `__trap_return`
/// then `mv sp, a0` onto the survivor before any restore/`sret`. Returning
/// the dying task's own frame would `sret` into a dead task.
pub fn kill_and_reschedule(cause: &str, sepc: usize, stval: usize) -> &'static mut TrapFrame {
    let t = current();
    println!(
        "task {} killed: {} sepc={:#x} stval={:#x}",
        t.id, cause, sepc, stval
    );
    let id = mark_exited();
    after_exit(id)
}

/// Mark the current task `Exited` and drop `CURRENT`. Does not resume anyone
/// — T2.9's scheduler picks the next Ready task here.
#[cfg_attr(
    any(
        feature = "panic-selftest",
        feature = "hang-selftest",
        feature = "stress",
        feature = "frame-exhaust-selftest",
        feature = "freeze-selftest"
    ),
    allow(dead_code)
)]
pub fn kill_unknown_syscall(num: usize, sepc: usize, stval: usize) -> usize {
    let t = current();
    println!(
        "task {} killed: unknown syscall {} sepc={:#x} stval={:#x}",
        t.id, num, sepc, stval
    );
    mark_exited()
}

/// Invalid user pointer (D-0034). `stval` is the pointer the task named;
/// hardware does not fill it on an `ecall`.
#[cfg_attr(
    any(
        feature = "panic-selftest",
        feature = "hang-selftest",
        feature = "stress",
        feature = "frame-exhaust-selftest",
        feature = "freeze-selftest"
    ),
    allow(dead_code)
)]
pub fn kill_invalid_user_ptr(sepc: usize, ptr: usize) {
    let t = current();
    println!(
        "task {} killed: invalid user pointer sepc={:#x} stval={:#x}",
        t.id, sepc, ptr
    );
    let _ = mark_exited();
}

/// Voluntary `exit`. Same Exited/CURRENT drop as a kill; the cause line
/// is `task N exit CODE` so T2.12 can grep it.
#[cfg_attr(
    any(
        feature = "panic-selftest",
        feature = "hang-selftest",
        feature = "stress",
        feature = "frame-exhaust-selftest",
        feature = "freeze-selftest"
    ),
    allow(dead_code)
)]
pub fn exit_current(code: usize) {
    let t = current();
    println!("task {} exit {}", t.id, code);
    t.exit_code = code;
    let _ = mark_exited();
}

/// Empty ready set after the last `exit` (or kill): SRST. No idle loop
/// (D-0035) — this *is* the scheduler's empty-set behavior.
#[cfg_attr(
    any(
        feature = "panic-selftest",
        feature = "hang-selftest",
        feature = "stress",
        feature = "frame-exhaust-selftest",
        feature = "freeze-selftest"
    ),
    allow(dead_code)
)]
pub fn stop_until_scheduler() -> ! {
    println!("no ready task; shutting down");
    let ret = crate::sbi::shutdown();
    panic!(
        "shutdown failed: SRST error={} value={}",
        ret.error, ret.value
    );
}
