//! Sv39 page tables: build the kernel map, walk it in software, then
//! activate it.
//!
//! Owns the root table and every intermediate table allocated for the M1
//! identity map (D-0019, D-0025, D-0026). `map` creates those tables from
//! the frame allocator; `walk` decodes raw PTEs by bit position and does
//! not call `map`'s helpers. `activate` is the only `satp` write: SIE off,
//! `csrw satp`, `sfence.vma`, SIE restored (D-0022). Without this module
//! there is no translation, and W^X / the guard hole are inert.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::csr;
use crate::frame::{self, PAGE_SIZE, RAM_END, RAM_START};
use crate::println;
use core::arch::asm;

/// Number of PTEs in a 4 KiB Sv39 table. Privileged spec 20211203 §4.3.2.
const PTES: usize = 512;
/// VPN bits per level. §4.4.1.
const VPN_BITS: usize = 9;
const VPN_MASK: usize = (1 << VPN_BITS) - 1;
/// Page offset bits. §4.4.1.
const OFF_BITS: usize = 12;
/// Sv39 walk depth: level 2 is the root, level 0 is the 4 KiB leaf. §4.4.1.
const ROOT_LEVEL: usize = 2;

/// Mapper flag bits. Privileged spec 20211203 §4.3.1 Table 4.2.
/// The walker below does **not** use these constants.
const FLAG_V: u64 = 1 << 0;
const FLAG_R: u64 = 1 << 1;
const FLAG_W: u64 = 1 << 2;
const FLAG_X: u64 = 1 << 3;
const FLAG_A: u64 = 1 << 6;
const FLAG_D: u64 = 1 << 7;

/// A+D on every leaf (D-0019), U=0, G=0. W implies R (reserved otherwise).
const LEAF_RX: u64 = FLAG_V | FLAG_R | FLAG_X | FLAG_A | FLAG_D;
const LEAF_R: u64 = FLAG_V | FLAG_R | FLAG_A | FLAG_D;
const LEAF_RW: u64 = FLAG_V | FLAG_R | FLAG_W | FLAG_A | FLAG_D;
/// Non-leaf: V=1, R=W=X=0 (concept 11.4).
const NONLEAF: u64 = FLAG_V;

/// Address past RAM. Unmapped. PLAN T1.6 probe.
const ABOVE_RAM: usize = 0x9000_0000;
/// QEMU `virt` UART. Unmapped (D-0025). Extra probe, not a required marker.
const UART_MMIO: usize = 0x1000_0000;

extern "C" {
    static __kernel_start: u8;
    static __rodata_start: u8;
    static __data_start: u8;
    static __bss_end: u8;
    static __boot_stack_bottom: u8;
    static __boot_stack_top: u8;
    static __heap_start: u8;
    static __heap_end: u8;
}

const _: () = assert!(PAGE_SIZE / core::mem::size_of::<u64>() == PTES);

static mut ROOT_PA: usize = 0;
static mut TABLES: usize = 0;

fn kernel_start() -> usize {
    core::ptr::addr_of!(__kernel_start) as usize
}
fn rodata_start() -> usize {
    core::ptr::addr_of!(__rodata_start) as usize
}
fn data_start() -> usize {
    core::ptr::addr_of!(__data_start) as usize
}
fn bss_end() -> usize {
    core::ptr::addr_of!(__bss_end) as usize
}
fn boot_stack_bottom() -> usize {
    core::ptr::addr_of!(__boot_stack_bottom) as usize
}
fn boot_stack_top() -> usize {
    core::ptr::addr_of!(__boot_stack_top) as usize
}
fn heap_start() -> usize {
    core::ptr::addr_of!(__heap_start) as usize
}
fn heap_end() -> usize {
    core::ptr::addr_of!(__heap_end) as usize
}

/// Physical address of the root table. Zero until `init`.
pub fn root_pa() -> usize {
    unsafe { ROOT_PA }
}

/// Table frames consumed by `init` (root + intermediates).
pub fn tables_used() -> usize {
    unsafe { TABLES }
}

/// `satp` value T1.7 will write: `MODE=8 << 60 | PPN(root)`. Not written here.
pub fn satp_value(root: usize) -> usize {
    (csr::satp::MODE_SV39 << csr::satp::MODE_SHIFT) | ((root >> OFF_BITS) & csr::satp::PPN_MASK)
}

fn alloc_table() -> usize {
    let pa = frame::alloc_frame();
    unsafe { TABLES += 1 };
    pa
}

fn vpn(va: usize, level: usize) -> usize {
    (va >> (OFF_BITS + VPN_BITS * level)) & VPN_MASK
}

fn pte_slot(table_pa: usize, index: usize) -> *mut u64 {
    unsafe { (table_pa as *mut u64).add(index) }
}

fn encode_leaf(pa: usize, flags: u64) -> u64 {
    ((pa as u64) >> OFF_BITS << 10) | flags
}

/// Identity-map `[va, pa]` at 4 KiB. Creates missing intermediates.
/// Panics on remap, on a superpage collision, or on W without R.
fn map(root: usize, va: usize, pa: usize, flags: u64) {
    if va % PAGE_SIZE != 0 || pa % PAGE_SIZE != 0 {
        panic!("map: unaligned va={:#x} pa={:#x}", va, pa);
    }
    if flags & FLAG_W != 0 && flags & FLAG_R == 0 {
        panic!("map: W without R va={:#x}", va);
    }
    if flags & (FLAG_R | FLAG_W | FLAG_X) == 0 {
        panic!("map: leaf with R=W=X=0 va={:#x}", va);
    }

    let mut table = root;
    for level in (1..=ROOT_LEVEL).rev() {
        let slot = pte_slot(table, vpn(va, level));
        let raw = unsafe { core::ptr::read(slot) };
        if raw == 0 {
            let next = alloc_table();
            unsafe { core::ptr::write(slot, encode_leaf(next, NONLEAF)) };
            table = next;
            continue;
        }
        if raw & (FLAG_R | FLAG_W | FLAG_X) != 0 {
            panic!("map: superpage at L{} va={:#x} pte={:#x}", level, va, raw);
        }
        if raw & FLAG_V == 0 {
            panic!("map: V=0 but nonzero pte={:#x} va={:#x}", raw, va);
        }
        table = ((raw >> 10) << OFF_BITS) as usize;
    }

    let slot = pte_slot(table, vpn(va, 0));
    let raw = unsafe { core::ptr::read(slot) };
    if raw != 0 {
        panic!("map: remap va={:#x} old={:#x}", va, raw);
    }
    unsafe { core::ptr::write(slot, encode_leaf(pa, flags)) };
}

fn map_range(root: usize, start: usize, end: usize, flags: u64) {
    if start % PAGE_SIZE != 0 || end % PAGE_SIZE != 0 || start >= end {
        panic!("map_range: [{:#x}, {:#x})", start, end);
    }
    let mut va = start;
    while va < end {
        map(root, va, va, flags);
        va += PAGE_SIZE;
    }
}

/// Build the kernel map from linker symbols. Does not write `satp`.
fn build() -> usize {
    let ks = kernel_start();
    let rs = rodata_start();
    let ds = data_start();
    let be = bss_end();
    let sb = boot_stack_bottom();
    let st = boot_stack_top();
    let hs = heap_start();
    let he = heap_end();

    if ks != 0x8020_0000 {
        panic!("__kernel_start={:#x}, expected 0x80200000", ks);
    }
    if be + PAGE_SIZE != sb {
        panic!(
            "guard hole: __bss_end={:#x} __boot_stack_bottom={:#x}",
            be, sb
        );
    }
    if st != hs {
        panic!("gap between stack and heap: top={:#x} heap={:#x}", st, hs);
    }
    if !(ks < rs && rs < ds && ds < be && be < sb && sb < st && hs < he && he < RAM_END) {
        panic!(
            "symbol order: ks={:#x} rs={:#x} ds={:#x} be={:#x} sb={:#x} st={:#x} hs={:#x} he={:#x}",
            ks, rs, ds, be, sb, st, hs, he
        );
    }

    let root = alloc_table();
    unsafe { ROOT_PA = root };

    map_range(root, ks, rs, LEAF_RX);
    map_range(root, rs, ds, LEAF_R);
    map_range(root, ds, be, LEAF_RW);
    // Guard [__bss_end, __boot_stack_bottom): no entry.
    map_range(root, sb, st, LEAF_RW);
    map_range(root, hs, he, LEAF_RW);
    map_range(root, he, RAM_END, LEAF_RW);
    // OpenSBI [RAM_START, ks) omitted. No MMIO (D-0025).

    root
}

// ---------------------------------------------------------------------------
// Software walker. Decodes by bit position. Does not use FLAG_* / encode_leaf
// / map. A shared off-by-one in those helpers would pass T1.6 and hang T1.7.
// Privileged spec 20211203 §4.3.1, §4.4.1.
// ---------------------------------------------------------------------------

const V_BIT: u32 = 0;
const R_BIT: u32 = 1;
const W_BIT: u32 = 2;
const X_BIT: u32 = 3;
const U_BIT: u32 = 4;
const G_BIT: u32 = 5;
const A_BIT: u32 = 6;
const D_BIT: u32 = 7;
const PPN_SHIFT: u32 = 10;
const PPN_BITS: u64 = 44;

fn pte_bit(raw: u64, bit: u32) -> bool {
    (raw >> bit) & 1 != 0
}

fn pte_ppn(raw: u64) -> u64 {
    (raw >> PPN_SHIFT) & ((1 << PPN_BITS) - 1)
}

enum Walk {
    Unmapped { level: usize, raw: u64 },
    Mapped { pa: usize, raw: u64, level: usize },
}

fn sv39_canonical(va: usize) -> bool {
    // Bits [63:39] must equal bit 38. §4.4.1.
    let top = va as i64 >> 38;
    top == 0 || top == -1
}

/// Walk `va` from `root`. Asserts non-leaf discipline at every intermediate.
fn walk(root: usize, va: usize) -> Walk {
    if !sv39_canonical(va) {
        panic!("walk: non-canonical va={:#x}", va);
    }
    let mut table = root;
    for level in (0..=2).rev() {
        // VA split by spec §4.4.1, not via `vpn()`: bits [20:12] L0, [29:21] L1,
        // [38:30] L2. Duplicated so a mapper index bug cannot hide here.
        let idx = (va >> (12 + 9 * level)) & 0x1ff;
        let raw = unsafe { core::ptr::read((table as *const u64).add(idx)) };
        if !pte_bit(raw, V_BIT) {
            return Walk::Unmapped { level, raw };
        }
        let r = pte_bit(raw, R_BIT);
        let w = pte_bit(raw, W_BIT);
        let x = pte_bit(raw, X_BIT);
        if r || w || x {
            if w && !r {
                panic!(
                    "walk: reserved W without R va={:#x} L{} pte={:#x}",
                    va, level, raw
                );
            }
            if level != 0 {
                panic!(
                    "walk: superpage leaf at L{} va={:#x} pte={:#x} (D-0026: 4 KiB only)",
                    level, va, raw
                );
            }
            let pa = ((pte_ppn(raw) as usize) << 12) | (va & 0xfff);
            return Walk::Mapped { pa, raw, level };
        }
        // Non-leaf: V=1, R=W=X=0. U/G/A/D reserved and must be zero (§4.3.1).
        if pte_bit(raw, U_BIT) || pte_bit(raw, G_BIT) || pte_bit(raw, A_BIT) || pte_bit(raw, D_BIT)
        {
            panic!(
                "walk: non-leaf U/G/A/D set va={:#x} L{} pte={:#x}",
                va, level, raw
            );
        }
        if level == 0 {
            panic!(
                "walk: L0 pointer PTE (V=1 R=W=X=0) va={:#x} pte={:#x}",
                va, raw
            );
        }
        table = (pte_ppn(raw) as usize) << 12;
    }
    panic!("walk: fell off va={:#x}", va);
}

#[derive(Clone, Copy)]
enum Expect {
    Unmapped,
    /// Identity-mapped 4 KiB leaf. A=1 D=1 U=0 required on every mapped probe.
    Mapped {
        r: bool,
        w: bool,
        x: bool,
    },
}

fn flags_match(raw: u64, r: bool, w: bool, x: bool) -> bool {
    pte_bit(raw, V_BIT)
        && pte_bit(raw, R_BIT) == r
        && pte_bit(raw, W_BIT) == w
        && pte_bit(raw, X_BIT) == x
        && !pte_bit(raw, U_BIT)
        && !pte_bit(raw, G_BIT)
        && pte_bit(raw, A_BIT)
        && pte_bit(raw, D_BIT)
}

fn fmt_flags(raw: u64) -> &'static str {
    // Fixed strings so the probe table stays column-aligned without alloc.
    match (
        pte_bit(raw, V_BIT),
        pte_bit(raw, R_BIT),
        pte_bit(raw, W_BIT),
        pte_bit(raw, X_BIT),
        pte_bit(raw, U_BIT),
        pte_bit(raw, A_BIT),
        pte_bit(raw, D_BIT),
    ) {
        (true, true, false, true, false, true, true) => "V R   X U=0 A D",
        (true, true, false, false, false, true, true) => "V R     U=0 A D",
        (true, true, true, false, false, true, true) => "V R W   U=0 A D",
        _ => "UNEXPECTED",
    }
}

fn probe(root: usize, name: &str, va: usize, expect: Expect) {
    match (walk(root, va), expect) {
        (Walk::Mapped { pa, raw, level }, Expect::Mapped { r, w, x }) => {
            if pa != va {
                panic!("{}: va={:#x} -> pa={:#x}, want identity", name, va, pa);
            }
            if !flags_match(raw, r, w, x) {
                panic!(
                    "{}: va={:#x} pte={:#x} flags mismatch (want R={} W={} X={} A=1 D=1 U=0)",
                    name, va, raw, r, w, x
                );
            }
            println!(
                "{:<16} {:#012x} -> {:#012x}  {}  L{}  {}",
                name,
                va,
                pa,
                fmt_flags(raw),
                level,
                "ok"
            );
        }
        (Walk::Unmapped { level, raw }, Expect::Unmapped) => {
            println!(
                "{:<16} {:#012x}    unmapped            V=0 at L{} pte={:#x}  ok",
                name, va, level, raw
            );
        }
        (Walk::Mapped { pa, raw, level }, Expect::Unmapped) => {
            panic!(
                "{}: va={:#x} mapped to {:#x} L{} pte={:#x}, want unmapped",
                name, va, pa, level, raw
            );
        }
        (Walk::Unmapped { level, raw }, Expect::Mapped { r, w, x }) => {
            panic!(
                "{}: va={:#x} unmapped at L{} pte={:#x}, want R={} W={} X={}",
                name, va, level, raw, r, w, x
            );
        }
    }
}

fn verify(root: usize) {
    println!("va               pa              flags            lvl");
    probe(
        root,
        "kernel entry",
        kernel_start(),
        Expect::Mapped {
            r: true,
            w: false,
            x: true,
        },
    );
    // Last byte of .text: still in the RX range, not the entry page if .text
    // spans more than one page.
    probe(
        root,
        ".text",
        rodata_start() - 1,
        Expect::Mapped {
            r: true,
            w: false,
            x: true,
        },
    );
    probe(
        root,
        ".rodata",
        rodata_start(),
        Expect::Mapped {
            r: true,
            w: false,
            x: false,
        },
    );
    probe(
        root,
        ".data/.bss",
        data_start(),
        Expect::Mapped {
            r: true,
            w: true,
            x: false,
        },
    );
    probe(
        root,
        "stack",
        boot_stack_top() - 0x10,
        Expect::Mapped {
            r: true,
            w: true,
            x: false,
        },
    );
    probe(
        root,
        "heap",
        heap_start(),
        Expect::Mapped {
            r: true,
            w: true,
            x: false,
        },
    );
    probe(
        root,
        "free frames",
        heap_end(),
        Expect::Mapped {
            r: true,
            w: true,
            x: false,
        },
    );
    probe(
        root,
        "last RAM",
        RAM_END - PAGE_SIZE,
        Expect::Mapped {
            r: true,
            w: true,
            x: false,
        },
    );
    probe(
        root,
        "page table",
        root,
        Expect::Mapped {
            r: true,
            w: true,
            x: false,
        },
    );
    probe(root, "guard", bss_end(), Expect::Unmapped);
    probe(root, "OpenSBI", RAM_START, Expect::Unmapped);
    probe(root, "above RAM", ABOVE_RAM, Expect::Unmapped);
    probe(root, "UART MMIO", UART_MMIO, Expect::Unmapped);
}

/// Build the map, print the `satp` we would write, walk the probes, print
/// `PAGETABLE OK`. Does not write `satp`.
pub fn init() {
    if unsafe { ROOT_PA } != 0 {
        panic!("page::init called twice");
    }
    let root = build();
    let satp = satp_value(root);
    let satp_now = csr::satp::read();
    if satp_now != 0 {
        panic!("satp changed to {:#x} during table build", satp_now);
    }
    println!(
        "root_pa={:#x} tables={} satp_would_write={:#x} (MODE=8 << 60 | PPN={:#x}, not written)",
        root_pa(),
        tables_used(),
        satp,
        root >> OFF_BITS
    );
    verify(root);
    println!("PAGETABLE OK");
}

/// Panic unless `va` identity-maps at L0 with the given R/W/X (A=1 D=1 U=0).
fn require_leaf(va: usize, r: bool, w: bool, x: bool, what: &str) {
    match walk(root_pa(), va) {
        Walk::Mapped { pa, raw, level } => {
            if pa != va || level != 0 || !flags_match(raw, r, w, x) {
                panic!(
                    "{}: va={:#x} -> pa={:#x} L{} pte={:#x}, want identity L0 R={} W={} X={}",
                    what, va, pa, level, raw, r, w, x
                );
            }
        }
        Walk::Unmapped { level, raw } => {
            panic!(
                "{}: va={:#x} unmapped at L{} pte={:#x}, want R={} W={} X={}",
                what, va, level, raw, r, w, x
            );
        }
    }
}

/// Write `satp`, `sfence.vma`, keep executing. D-0022: SIE is clear across
/// the four-instruction window so a tick cannot vector through an unfenced
/// translation. `sfence.vma x0, x0` is *after* the write — QEMU flushes on
/// `satp` writes in practice, the spec does not promise it.
#[inline(never)]
pub fn activate() {
    let root = root_pa();
    if root == 0 {
        panic!("page::activate before init");
    }
    let satp = satp_value(root);
    if (satp >> csr::satp::MODE_SHIFT) != csr::satp::MODE_SV39 {
        panic!("satp MODE is not 8: {:#x}", satp);
    }
    if (satp & csr::satp::PPN_MASK) != (root >> OFF_BITS) {
        panic!(
            "satp PPN={:#x} != root>>12 {:#x}",
            satp & csr::satp::PPN_MASK,
            root >> OFF_BITS
        );
    }

    let sp: usize;
    unsafe {
        asm!(
            "mv {sp}, sp",
            sp = out(reg) sp,
            options(nomem, nostack, preserves_flags)
        );
    }
    require_leaf(
        activate as *const () as usize,
        true,
        false,
        true,
        "activate PC",
    );
    require_leaf(crate::trap::entry_pa(), true, false, true, "stvec");
    require_leaf(sp, true, true, false, "sp");

    let sie_bit = csr::sstatus::SIE;
    let sie_was = csr::sstatus::read() & sie_bit;
    unsafe {
        asm!(
            "csrc sstatus, {sie}",
            "csrw satp, {satp}",
            "sfence.vma x0, x0",
            "csrs sstatus, {sie_was}",
            sie = in(reg) sie_bit,
            satp = in(reg) satp,
            sie_was = in(reg) sie_was,
            options(nostack, preserves_flags),
        );
    }
    let got = csr::satp::read();
    if got != satp {
        panic!("satp wrote {:#x}, read {:#x}", satp, got);
    }
    println!("PAGING OK");
}
