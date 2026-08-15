//! Split virtqueues and the static DMA pool (D-0038).
//!
//! Owns the page-aligned `.bss` pool (RX 16×2048, TX 8×2048, three ring
//! structures per queue), the descriptor tables that point into it, and
//! `verify()` — alignment, pool membership, identity map, idx == 0, and
//! the six queue-address register readbacks. Writes those registers
//! before `verify` and never sets QueueReady or DRIVER_OK: checking a
//! ring the device has already been told to use is too late. Barriers
//! live here from day one because QEMU will not catch their absence.
//! Without this module the NIC has nowhere legal to DMA.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::frame::PAGE_SIZE;
use crate::page;
use crate::println;
use crate::virtio;
use core::arch::asm;
use core::ptr::{addr_of, addr_of_mut};

/// Descriptors per queue. D-0038.
const QSIZE: usize = 16;
/// RX buffers: one per RX descriptor, whole-frame, no `MRG_RXBUF`.
const RX_BUFS: usize = 16;
/// TX buffers. Fewer than `QSIZE`; unused TX descriptors stay addr=0.
const TX_BUFS: usize = 8;
/// Bytes per buffer. 2048 holds a 12-byte virtio-net header plus an MTU
/// frame without chaining.
const BUF: usize = 2048;
/// virtio-net receiveq. Virtio 1.2 §5.1.2.
const Q_RX: u32 = 0;
/// virtio-net transmitq. §5.1.2.
const Q_TX: u32 = 1;
/// Device writes this buffer. Virtio 1.2 §2.7.5 `VIRTQ_DESC_F_WRITE`.
const DESC_WRITE: u16 = 1;

const _: () = assert!(RX_BUFS == QSIZE);
const _: () = assert!(TX_BUFS <= QSIZE);
const _: () = assert!(BUF == 2048);

/// Descriptor table entry. Virtio 1.2 §2.7.5. 16 bytes, align 16.
#[repr(C, align(16))]
#[derive(Clone, Copy)]
struct Desc {
    addr: u64,
    len: u32,
    flags: u16,
    next: u16,
}

/// Avail (driver) ring. §2.7.6. Align 2. No `used_event` — we do not
/// negotiate `EVENT_IDX`.
#[repr(C, align(2))]
struct Avail {
    flags: u16,
    idx: u16,
    ring: [u16; QSIZE],
}

/// Used-ring element. §2.7.8.
#[repr(C)]
#[derive(Clone, Copy)]
struct UsedElem {
    id: u32,
    len: u32,
}

/// Used (device) ring. §2.7.8. Align 4. No `avail_event` (same reason).
#[repr(C, align(4))]
struct Used {
    flags: u16,
    idx: u16,
    ring: [UsedElem; QSIZE],
}

/// One split virtqueue: descriptor table + avail + used.
#[repr(C, align(16))]
struct Queue {
    desc: [Desc; QSIZE],
    avail: Avail,
    used: Used,
}

/// The DMA pool. Alignment is structural: the linker places this on a
/// 4 KiB boundary because of `repr(align(4096))`, not because `verify`
/// rounds anything. `verify`'s 16/2/4 checks are belt-and-braces.
#[repr(C, align(4096))]
struct Pool {
    rx: Queue,
    tx: Queue,
    rx_buf: [[u8; BUF]; RX_BUFS],
    tx_buf: [[u8; BUF]; TX_BUFS],
}

const _: () = assert!(core::mem::size_of::<Desc>() == 16);
const _: () = assert!(core::mem::align_of::<Desc>() == 16);
const _: () = assert!(core::mem::align_of::<Avail>() >= 2);
const _: () = assert!(core::mem::align_of::<Used>() >= 4);
const _: () = assert!(core::mem::align_of::<Pool>() == 4096);

const EMPTY_DESC: Desc = Desc {
    addr: 0,
    len: 0,
    flags: 0,
    next: 0,
};
const EMPTY_USED: UsedElem = UsedElem { id: 0, len: 0 };

impl Queue {
    const fn new() -> Self {
        Self {
            desc: [EMPTY_DESC; QSIZE],
            avail: Avail {
                flags: 0,
                idx: 0,
                ring: [0; QSIZE],
            },
            used: Used {
                flags: 0,
                idx: 0,
                ring: [EMPTY_USED; QSIZE],
            },
        }
    }
}

static mut POOL: Pool = Pool {
    rx: Queue::new(),
    tx: Queue::new(),
    rx_buf: [[0; BUF]; RX_BUFS],
    tx_buf: [[0; BUF]; TX_BUFS],
};

extern "C" {
    static __bss_end: u8;
    static __boot_stack_bottom: u8;
    static __heap_start: u8;
}

fn pool() -> &'static mut Pool {
    unsafe { &mut *addr_of_mut!(POOL) }
}

fn pa<T>(p: *const T) -> usize {
    p as usize
}

fn pool_range() -> (usize, usize) {
    let start = pa(addr_of!(POOL));
    (start, start + core::mem::size_of::<Pool>())
}

/// True iff `[addr, addr+len)` lies inside the pool. GPA == VA (identity).
fn in_pool(addr: u64, len: u32) -> bool {
    let (lo, hi) = pool_range();
    let a = addr as usize;
    let end = match a.checked_add(len as usize) {
        Some(e) => e,
        None => return false,
    };
    a >= lo && end <= hi
}

/// Fill RX/TX descriptor addresses. Does not touch `avail.idx` — the
/// device is not yet allowed to consume anything.
fn fill_descriptors(p: &mut Pool) {
    for i in 0..RX_BUFS {
        p.rx.desc[i] = Desc {
            addr: pa(p.rx_buf[i].as_ptr()) as u64,
            len: BUF as u32,
            flags: DESC_WRITE,
            next: 0,
        };
    }
    for i in 0..TX_BUFS {
        p.tx.desc[i] = Desc {
            addr: pa(p.tx_buf[i].as_ptr()) as u64,
            len: BUF as u32,
            flags: 0,
            next: 0,
        };
    }
}

/// Write a 64-bit GPA as low-then-high at `off_low`. The two halves are
/// never written independently, so swapped high/low is unrepresentable
/// at the call site. Wrong `off_low` is the remaining silent killer —
/// `verify` reads both halves back.
fn write_addr(base: usize, off_low: usize, gpa: usize, name: &str, q: u32) {
    let lo = gpa as u32;
    let hi = (gpa >> 32) as u32;
    virtio::write32(base, off_low, lo);
    virtio::write32(base, off_low + 4, hi);
    println!("virtq: q{q} {name} write {gpa:#x} lo={lo:#x} hi={hi:#x} @{off_low:#x}");
}

/// Read the two halves back. Virtio 1.2 §4.2.2 marks these write-only;
/// QEMU returns 0 and logs `LOG_GUEST_ERROR`. A zero read is that
/// transport, not proof the write stuck. A nonzero mismatch is a panic.
fn readback(base: usize, off_low: usize, gpa: usize, name: &str, q: u32) {
    let want_lo = gpa as u32;
    let want_hi = (gpa >> 32) as u32;
    let got_lo = virtio::read32(base, off_low);
    let got_hi = virtio::read32(base, off_low + 4);
    println!(
        "virtq: q{q} {name} read  lo={got_lo:#x} hi={got_hi:#x} (want lo={want_lo:#x} hi={want_hi:#x})"
    );
    check_half(q, name, "low", off_low, want_lo, got_lo);
    check_half(q, name, "high", off_low + 4, want_hi, got_hi);
}

fn check_half(q: u32, name: &str, half: &str, off: usize, want: u32, got: u32) {
    if got == want {
        return;
    }
    if got == 0 {
        // Write-only MMIO: not a match, and not distinguishable from a
        // write that missed the register. Named offsets + write_addr
        // are the load-bearing guard on this transport (D-0038).
        println!(
            "virtq: q{q} {name} {half} @{off:#x} wrote={want:#x} read=0 (write-only MMIO, not a match)"
        );
        return;
    }
    panic!(
        "virtq: q{q} {name} {half} @{off:#x} wrote={want:#x} read={got:#x}; wrong offset or swapped high/low"
    );
}

fn write_queue_addrs(base: usize, q: u32, queue: &Queue) {
    virtio::write32(base, virtio::OFF_QUEUE_SEL, q);
    let ready = virtio::read32(base, virtio::OFF_QUEUE_READY);
    if ready != 0 {
        panic!("virtq: q{q} QueueReady={ready} before address write; device already owns the ring");
    }
    write_addr(
        base,
        virtio::OFF_QUEUE_DESC_LOW,
        pa(queue.desc.as_ptr()),
        "QueueDesc",
        q,
    );
    write_addr(
        base,
        virtio::OFF_QUEUE_DRIVER_LOW,
        pa(&queue.avail),
        "QueueDriver",
        q,
    );
    write_addr(
        base,
        virtio::OFF_QUEUE_DEVICE_LOW,
        pa(&queue.used),
        "QueueDevice",
        q,
    );
}

fn readback_queue(base: usize, q: u32, queue: &Queue) {
    virtio::write32(base, virtio::OFF_QUEUE_SEL, q);
    let ready = virtio::read32(base, virtio::OFF_QUEUE_READY);
    if ready != 0 {
        panic!("virtq: q{q} QueueReady={ready} at verify; device already owns the ring");
    }
    readback(
        base,
        virtio::OFF_QUEUE_DESC_LOW,
        pa(queue.desc.as_ptr()),
        "QueueDesc",
        q,
    );
    readback(
        base,
        virtio::OFF_QUEUE_DRIVER_LOW,
        pa(&queue.avail),
        "QueueDriver",
        q,
    );
    readback(
        base,
        virtio::OFF_QUEUE_DEVICE_LOW,
        pa(&queue.used),
        "QueueDevice",
        q,
    );
}

fn require_align(addr: usize, align: usize, what: &str) {
    if addr % align != 0 {
        panic!("virtq: {what} at {addr:#x} not aligned to {align}");
    }
}

fn check_queue(name: &str, q: &Queue, filled: usize) {
    require_align(pa(q.desc.as_ptr()), 16, name);
    require_align(pa(&q.avail), 2, name);
    require_align(pa(&q.used), 4, name);
    page::require_identity_rw_range(
        pa(q.desc.as_ptr()),
        pa(q.desc.as_ptr()) + core::mem::size_of_val(&q.desc),
        name,
    );
    page::require_identity_rw_range(
        pa(&q.avail),
        pa(&q.avail) + core::mem::size_of::<Avail>(),
        name,
    );
    page::require_identity_rw_range(pa(&q.used), pa(&q.used) + core::mem::size_of::<Used>(), name);
    if q.avail.idx != 0 || q.used.idx != 0 {
        panic!(
            "virtq: {name} avail.idx={} used.idx={} want 0,0",
            q.avail.idx, q.used.idx
        );
    }
    for i in 0..QSIZE {
        let d = &q.desc[i];
        if i < filled {
            if d.addr == 0 || !in_pool(d.addr, d.len) {
                panic!(
                    "virtq: {name} desc[{i}] addr={:#x} len={} outside pool {:#x}..{:#x}",
                    d.addr,
                    d.len,
                    pool_range().0,
                    pool_range().1
                );
            }
            page::require_identity_rw(d.addr as usize, name);
            if d.len != 0 {
                page::require_identity_rw(d.addr as usize + d.len as usize - 1, name);
            }
        } else if d.addr != 0 {
            panic!(
                "virtq: {name} desc[{i}] unused but addr={:#x} (want 0)",
                d.addr
            );
        }
    }
}

/// Guest-side checks, then the six-register readback. Called after the
/// address registers are written and while QueueReady is still 0.
fn verify(base: usize, p: &Pool) {
    let (lo, hi) = pool_range();
    if lo % PAGE_SIZE != 0 {
        panic!("virtq: pool at {lo:#x} not page-aligned (repr(align(4096)) failed)");
    }
    page::require_identity_rw_range(lo, hi, "dma pool");
    check_queue("rx", &p.rx, RX_BUFS);
    check_queue("tx", &p.tx, TX_BUFS);
    readback_queue(base, Q_RX, &p.rx);
    readback_queue(base, Q_TX, &p.tx);
}

/// Fence + store `avail.idx`. Not called until T3.4; present now because
/// a missing `fence w,w` cannot be provoked on QEMU (PLAN concept 3).
#[allow(dead_code)]
fn publish_avail_idx(avail: &mut Avail, idx: u16) {
    // Descriptor and `avail.ring[]` stores must be visible to the device
    // before it observes the new idx. QEMU's device model is synchronous
    // and will not reorder guest RAM writes against this store, so a
    // missing fence will not fail here.
    unsafe { asm!("fence w,w", options(nostack, preserves_flags)) };
    unsafe { core::ptr::write_volatile(&mut avail.idx, idx) };
}

/// Fence + QueueNotify. Not called until T3.4; same concept-3 reason.
#[allow(dead_code)]
fn notify(base: usize, q: u32) {
    // The idx store must reach RAM before the device observes the MMIO
    // doorbell. QEMU handles notify in the same CPU thread as the store,
    // so a missing `fence w,o` will not fail here.
    unsafe { asm!("fence w,o", options(nostack, preserves_flags)) };
    virtio::write32(base, virtio::OFF_QUEUE_NOTIFY, q);
}

/// Load `used.idx`, then fence before the caller reads `used.ring`.
/// Not called until T3.5; same concept-3 reason.
#[allow(dead_code)]
fn load_used_idx(used: &Used) -> u16 {
    let idx = unsafe { core::ptr::read_volatile(&used.idx) };
    // idx must be read before the `used.ring` entry the device just
    // wrote. QEMU completes that write before returning from notify, so
    // a missing `fence r,r` will not fail here.
    unsafe { asm!("fence r,r", options(nostack, preserves_flags)) };
    idx
}

/// Build the pool, program the six queue-address registers per queue
/// with QueueReady left at 0, then `verify()`. Does not set DRIVER_OK.
pub fn init() {
    let p = pool();
    let (lo, hi) = pool_range();
    let bss_end = pa(addr_of!(__bss_end));
    let stack_bottom = pa(addr_of!(__boot_stack_bottom));
    let heap_start = pa(addr_of!(__heap_start));
    if bss_end + PAGE_SIZE != stack_bottom {
        panic!(
            "virtq: guard hole moved: bss_end={bss_end:#x} stack_bottom={stack_bottom:#x}"
        );
    }
    println!(
        "virtq: pool {lo:#x}..{hi:#x} ({} bytes) bss_end={bss_end:#x} guard={stack_bottom:#x} heap_start={heap_start:#x}",
        hi - lo
    );

    fill_descriptors(p);
    let base = virtio::net_base();
    // Address registers first, verify second, QueueReady/DRIVER_OK never
    // in this task. The device must not own the ring while we check it.
    write_queue_addrs(base, Q_RX, &p.rx);
    write_queue_addrs(base, Q_TX, &p.tx);
    verify(base, p);
    println!("VIRTQ OK");
}
