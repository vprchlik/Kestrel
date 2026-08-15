//! virtio-net device: status handshake to `DRIVER_OK` (D-0038, D-0040).
//!
//! Owns the feature negotiation, QueueReady, RX posting, MAC print, and
//! `dump` / stall observability. The rings themselves stay in `virtq`.
//! `FEATURES_OK` readback is the one place the handshake can tell us the
//! device rejected our feature set — everything else is programming we
//! control. Without this module the NIC never leaves reset.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::csr;
use crate::println;
use crate::virtio;
use crate::virtq;

/// ACKNOWLEDGE. Virtio 1.2 §2.1.
const ACKNOWLEDGE: u32 = 1;
/// DRIVER. §2.1.
const DRIVER: u32 = 2;
/// DRIVER_OK. §2.1.
const DRIVER_OK: u32 = 4;
/// FEATURES_OK. §2.1. The loud failure point: the device clears this if
/// it cannot live with the features we wrote.
const FEATURES_OK: u32 = 8;
/// FAILED. §2.1.
const FAILED: u32 = 128;

/// `VIRTIO_NET_F_MAC` — bit 5, word 0. §5.1.3.
const F_MAC: u32 = 1 << 5;
/// `VIRTIO_NET_F_MRG_RXBUF` — bit 15, word 0. We decline it (D-0038).
const F_MRG_RXBUF: u32 = 1 << 15;
/// `VIRTIO_F_VERSION_1` — bit 32, so bit 0 of word 1. §6.
const F_VERSION_1: u32 = 1 << 0;
/// `VIRTIO_NET_F_STATUS` — bit 16, word 0. Declined.
const F_STATUS: u32 = 1 << 16;
/// `VIRTIO_F_RING_EVENT_IDX` — bit 29, word 0. Declined.
const F_EVENT_IDX: u32 = 1 << 29;

/// 100 ms at the 10 MHz `rdtime` timebase (`timer::PERIOD` is 10 ms).
const STALL_TICKS: usize = 1_000_000;

static mut RX_POSTED: u32 = 0;
static mut RX_COMPLETED: u32 = 0;
static mut TX_POSTED: u32 = 0;
static mut TX_COMPLETED: u32 = 0;
static mut STALL_ARMED_TIME: usize = 0;
static mut STALL_USED_AT_ARM: u16 = 0;
static mut STALL_DUMPED: bool = false;

fn status(base: usize) -> u32 {
    virtio::read32(base, virtio::OFF_STATUS)
}

fn write_status(base: usize, st: u32) -> u32 {
    virtio::write32(base, virtio::OFF_STATUS, st);
    let got = status(base);
    if got & FAILED != 0 {
        panic!("virtio-net: FAILED status={got:#x} after write {st:#x}");
    }
    got
}

fn read_mac(base: usize) -> [u8; 6] {
    let mut mac = [0u8; 6];
    for (i, b) in mac.iter_mut().enumerate() {
        *b = virtio::read8(base, virtio::OFF_CONFIG + i);
    }
    mac
}

/// Read both feature words. VERSION_1 lives in word 1 (bit 32).
fn read_device_features(base: usize) -> (u32, u32) {
    virtio::write32(base, virtio::OFF_DEVICE_FEATURES_SEL, 0);
    let w0 = virtio::read32(base, virtio::OFF_DEVICE_FEATURES);
    virtio::write32(base, virtio::OFF_DEVICE_FEATURES_SEL, 1);
    let w1 = virtio::read32(base, virtio::OFF_DEVICE_FEATURES);
    (w0, w1)
}

fn write_driver_features(base: usize, w0: u32, w1: u32) {
    virtio::write32(base, virtio::OFF_DRIVER_FEATURES_SEL, 0);
    virtio::write32(base, virtio::OFF_DRIVER_FEATURES, w0);
    virtio::write32(base, virtio::OFF_DRIVER_FEATURES_SEL, 1);
    virtio::write32(base, virtio::OFF_DRIVER_FEATURES, w1);
}

/// Status, ISR, both rings' shadow indices, posted/completed counters.
pub fn dump() {
    let base = virtio::net_base();
    let st = status(base);
    let isr = virtio::read32(base, virtio::OFF_INTERRUPT_STATUS);
    let rx_a = virtq::rx_avail_idx();
    let rx_u = virtq::rx_used_idx();
    let tx_a = virtq::tx_avail_idx();
    let tx_u = virtq::tx_used_idx();
    let rx_p = unsafe { RX_POSTED };
    let rx_c = unsafe { RX_COMPLETED };
    let tx_p = unsafe { TX_POSTED };
    let tx_c = unsafe { TX_COMPLETED };
    println!(
        "net: dump status={st:#x} isr={isr:#x} rx avail={rx_a} used={rx_u} posted={rx_p} completed={rx_c} tx avail={tx_a} used={tx_u} posted={tx_p} completed={tx_c}"
    );
}

/// If TX has been posted and `used.idx` has not moved for ~100 ms of
/// `rdtime`, print `dump` once. Never panics: the device is not obligated
/// to be fast; the diagnostic must exist.
pub fn poll_stall() {
    let posted = unsafe { TX_POSTED };
    if posted == 0 {
        return;
    }
    let used = virtq::tx_used_idx();
    let now = csr::time::read();
    let armed = unsafe { STALL_ARMED_TIME };
    let used_at_arm = unsafe { STALL_USED_AT_ARM };
    if used != used_at_arm {
        unsafe {
            STALL_ARMED_TIME = now;
            STALL_USED_AT_ARM = used;
        }
        return;
    }
    if armed == 0 {
        unsafe {
            STALL_ARMED_TIME = now;
            STALL_USED_AT_ARM = used;
        }
        return;
    }
    if now.wrapping_sub(armed) < STALL_TICKS {
        return;
    }
    if unsafe { STALL_DUMPED } {
        return;
    }
    unsafe { STALL_DUMPED = true };
    println!(
        "net: stall tx posted={posted} used.idx={used} unmoved for ~100ms"
    );
    dump();
}

/// Status handshake, queues, RX post, `DRIVER_OK`. Reset wipes the T3.2
/// address registers; we program them again after FEATURES_OK.
pub fn init() {
    let base = virtio::net_base();

    let got = write_status(base, 0);
    if got != 0 {
        panic!("virtio-net: reset did not stick status={got:#x}");
    }
    println!("virtio-net: reset status=0");

    let got = write_status(base, ACKNOWLEDGE);
    println!("virtio-net: ACKNOWLEDGE status={got:#x}");
    let got = write_status(base, ACKNOWLEDGE | DRIVER);
    println!("virtio-net: DRIVER status={got:#x}");

    let (dev0, dev1) = read_device_features(base);
    println!("virtio-net: device features word0={dev0:#x} word1={dev1:#x}");
    println!(
        "virtio-net: offered MAC={} MRG_RXBUF={} STATUS={} EVENT_IDX={} VERSION_1={}",
        dev0 & F_MAC != 0,
        dev0 & F_MRG_RXBUF != 0,
        dev0 & F_STATUS != 0,
        dev0 & F_EVENT_IDX != 0,
        dev1 & F_VERSION_1 != 0
    );
    if dev0 & F_MAC == 0 {
        panic!("virtio-net: device does not offer NET_F_MAC (word0={dev0:#x})");
    }
    if dev1 & F_VERSION_1 == 0 {
        panic!("virtio-net: device does not offer VERSION_1 (word1={dev1:#x})");
    }
    let take0 = F_MAC;
    let take1 = F_VERSION_1;
    println!(
        "virtio-net: driver features word0={take0:#x} word1={take1:#x} (MAC|VERSION_1 only); declining word0={:#x} word1={:#x}",
        dev0 & !take0,
        dev1 & !take1
    );
    write_driver_features(base, take0, take1);

    // THE loud failure point: a device that cannot live with that set
    // clears FEATURES_OK. Everything after this assumes the bit stuck.
    let got = write_status(base, ACKNOWLEDGE | DRIVER | FEATURES_OK);
    if got & FEATURES_OK == 0 {
        panic!(
            "virtio-net: FEATURES_OK cleared after write (status={got:#x}); device rejected VERSION_1|NET_F_MAC"
        );
    }
    println!("virtio-net: FEATURES_OK status={got:#x}");

    let n = virtq::QSIZE as u32;
    virtq::set_queue_num(base, virtq::Q_RX, n);
    virtq::set_queue_num(base, virtq::Q_TX, n);
    virtq::program_addrs(base);
    virtq::verify(base);
    println!("virtq: verify after FEATURES_OK (QueueReady still 0)");

    // Nothing between verify and QueueReady touches the ring.
    virtq::set_queue_ready(base, virtq::Q_RX);
    virtq::set_queue_ready(base, virtq::Q_TX);
    println!("virtio-net: QueueReady=1 (device owns the rings)");

    // First fence w,w: ring[] stores, then idx. No notify yet (3.1.1).
    let idx = virtq::post_rx();
    unsafe { RX_POSTED = idx as u32 };
    println!("virtio-net: posted {idx} RX buffers avail.idx={idx}");

    let got = write_status(base, ACKNOWLEDGE | DRIVER | FEATURES_OK | DRIVER_OK);
    if got & DRIVER_OK == 0 {
        panic!("virtio-net: DRIVER_OK did not stick status={got:#x}");
    }
    println!("virtio-net: DRIVER_OK status={got:#x}");
    println!("DRIVER_OK");

    virtq::notify(base, virtq::Q_RX);

    let mac = read_mac(base);
    println!(
        "virtio-net: mac {:02x}:{:02x}:{:02x}:{:02x}:{:02x}:{:02x}",
        mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]
    );
    if mac.iter().all(|&b| b == 0) {
        panic!("virtio-net: MAC is all zeros; NET_F_MAC negotiated but config empty");
    }

    dump();
    poll_stall();
}
