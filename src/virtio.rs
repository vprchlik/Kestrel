//! Virtio-mmio transport window and discovery probe (D-0039).
//!
//! Owns the eight-page MMIO window constants QEMU `virt` uses for
//! virtio-mmio, and the post-activation probe that reads Magic / Version /
//! DeviceID in that order. `page::build` maps the window from these
//! constants before `activate`, so D-0031's ban on later PTE edits stands.
//! Without this module the walker has nothing to assert and a netless
//! harness looks like a working boot.

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

const _: () = assert!(MMIO_END == MMIO_BASE + N_TRANSPORTS * STRIDE);
const _: () = assert!(MAGIC == u32::from_le_bytes(*b"virt"));

/// Read one 32-bit MMIO register. `base` is a mapped transport; `off` is
/// 4-byte aligned. Volatile: the device, not RAM, answers.
#[inline]
fn read32(base: usize, off: usize) -> u32 {
    let p = (base + off) as *const u32;
    unsafe { core::ptr::read_volatile(p) }
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
        Some(n) => println!("virtio-mmio: net at slot {n}"),
        None => panic!(
            "virtio-mmio: no net device (device ID {DEVICE_NET}) in {N_TRANSPORTS} slots"
        ),
    }
}
