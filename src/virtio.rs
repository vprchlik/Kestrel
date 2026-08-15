//! Virtio-mmio transport window, MMIO accessors, and discovery probe (D-0039).
//!
//! Owns the eight-page MMIO window constants QEMU `virt` uses for
//! virtio-mmio, the 32-bit register accessors the virtqueue code uses, and
//! the post-activation probe that reads Magic / Version / DeviceID in that
//! order. `page::build` maps the window from these constants before
//! `activate`, so D-0031's ban on later PTE edits stands. Without this
//! module the walker has nothing to assert and a netless harness looks
//! like a working boot.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::println;

/// First virtio-mmio transport. QEMU `hw/riscv/virt.c` (`VIRT_MMIO`).
pub const MMIO_BASE: usize = 0x1000_1000;
/// Byte stride between transports. Same file; each slot is one 4 KiB page.
pub const STRIDE: usize = 0x1000;
/// QEMU `virt` instantiates this many transports (`VIRTIO_COUNT`).
pub const N_TRANSPORTS: usize = 8;
/// Exclusive end of the window: `MMIO_BASE + N_TRANSPORTS * STRIDE`.
pub const MMIO_END: usize = 0x1000_9000;

/// MagicValue at offset 0. Virtio 1.2 §4.2.2.1; little-endian `"virt"`.
const MAGIC: u32 = 0x7472_6976;
/// Version register: 2 is modern (non-legacy) MMIO. §4.2.2.2.
const VERSION_MODERN: u32 = 2;
/// Network card. Virtio 1.2 §5.1 / device ID table. 0 means no device.
const DEVICE_NET: u32 = 1;

const OFF_MAGIC: usize = 0x000;
const OFF_VERSION: usize = 0x004;
const OFF_DEVICE_ID: usize = 0x008;
/// Queue selector. Virtio 1.2 §4.2.2; write-only.
pub(crate) const OFF_QUEUE_SEL: usize = 0x030;
/// QueueNumMax. §4.2.2; read-only.
pub(crate) const OFF_QUEUE_NUM_MAX: usize = 0x034;
/// QueueNum. §4.2.2; write-only.
pub(crate) const OFF_QUEUE_NUM: usize = 0x038;
/// QueueReady. §4.2.2; read/write. Zero until we enable the queue.
pub(crate) const OFF_QUEUE_READY: usize = 0x044;
/// QueueNotify. §4.2.2; write-only.
pub(crate) const OFF_QUEUE_NOTIFY: usize = 0x050;
/// InterruptStatus. §4.2.2; read-only.
pub(crate) const OFF_INTERRUPT_STATUS: usize = 0x060;
/// Device status. §4.2.2 / §2.1; read/write. Writing 0 is reset.
pub(crate) const OFF_STATUS: usize = 0x070;
/// DeviceFeatures. §4.2.2; read-only. Indexed by DeviceFeaturesSel.
pub(crate) const OFF_DEVICE_FEATURES: usize = 0x010;
/// DeviceFeaturesSel. §4.2.2; write-only.
pub(crate) const OFF_DEVICE_FEATURES_SEL: usize = 0x014;
/// DriverFeatures. §4.2.2; write-only. Indexed by DriverFeaturesSel.
pub(crate) const OFF_DRIVER_FEATURES: usize = 0x020;
/// DriverFeaturesSel. §4.2.2; write-only.
pub(crate) const OFF_DRIVER_FEATURES_SEL: usize = 0x024;
/// Descriptor table GPA, low 32 bits. §4.2.2 QueueDescLow; write-only.
pub(crate) const OFF_QUEUE_DESC_LOW: usize = 0x080;
/// Avail (driver) ring GPA, low 32 bits. §4.2.2 QueueDriverLow; write-only.
pub(crate) const OFF_QUEUE_DRIVER_LOW: usize = 0x090;
/// Used (device) ring GPA, low 32 bits. §4.2.2 QueueDeviceLow; write-only.
pub(crate) const OFF_QUEUE_DEVICE_LOW: usize = 0x0a0;
/// Device-specific configuration space. §4.2.2. virtio-net MAC starts here.
pub(crate) const OFF_CONFIG: usize = 0x100;

const _: () = assert!(MMIO_END == MMIO_BASE + N_TRANSPORTS * STRIDE);
const _: () = assert!(MAGIC == u32::from_le_bytes(*b"virt"));

static mut NET_BASE: usize = 0;

/// Read one 32-bit MMIO register. `base` is a mapped transport; `off` is
/// 4-byte aligned. Volatile: the device, not RAM, answers.
#[inline]
pub(crate) fn read32(base: usize, off: usize) -> u32 {
    let p = (base + off) as *const u32;
    unsafe { core::ptr::read_volatile(p) }
}

/// Write one 32-bit MMIO register. Same volatility rule as `read32`.
#[inline]
pub(crate) fn write32(base: usize, off: usize, val: u32) {
    let p = (base + off) as *mut u32;
    unsafe { core::ptr::write_volatile(p, val) };
}

/// Byte read. Config space (MAC) is a byte array, not a little-endian word.
#[inline]
pub(crate) fn read8(base: usize, off: usize) -> u8 {
    let p = (base + off) as *const u8;
    unsafe { core::ptr::read_volatile(p) }
}

/// MMIO base of the net transport. Panics if `probe` has not found one.
pub(crate) fn net_base() -> usize {
    let b = unsafe { NET_BASE };
    if b == 0 {
        panic!("virtio::net_base before probe found a net device");
    }
    b
}

fn device_name(id: u32) -> &'static str {
    match id {
        0 => "empty",
        DEVICE_NET => "net",
        _ => "other",
    }
}

/// Probe all eight transports after paging is live. Magic is read first.
/// A slot with no backend still returns MagicValue — DeviceID 0 is empty,
/// a wrong magic is a broken transport. Version must be 2 on every slot
/// with valid magic (a missing `force-legacy=false` fails here, not later).
/// Panics if no slot is a net device.
pub fn probe() {
    let mut net_slot: Option<usize> = None;
    for i in 0..N_TRANSPORTS {
        let base = MMIO_BASE + i * STRIDE;
        // Magic first — do not touch Version or DeviceID until it matches.
        let magic = read32(base, OFF_MAGIC);
        if magic != MAGIC {
            panic!(
                "virtio-mmio {i} {base:#x} magic={magic:#x} (want {MAGIC:#x} \"virt\"); empty slots still return magic, so this transport is broken"
            );
        }
        let version = read32(base, OFF_VERSION);
        if version != VERSION_MODERN {
            panic!(
                "virtio-mmio {i} {base:#x} version={version} (need {VERSION_MODERN} / modern); check -global virtio-mmio.force-legacy=false"
            );
        }
        let device = read32(base, OFF_DEVICE_ID);
        println!(
            "virtio-mmio {i} {base:#x} magic={magic:#010x} version={version} device={device} ({})",
            device_name(device)
        );
        if device == DEVICE_NET {
            net_slot = Some(i);
        }
    }
    match net_slot {
        Some(n) => {
            unsafe { NET_BASE = MMIO_BASE + n * STRIDE };
            println!("virtio-mmio: net at slot {n}");
        }
        None => panic!(
            "virtio-mmio: no net device (device ID {DEVICE_NET}) in {N_TRANSPORTS} slots"
        ),
    }
}
