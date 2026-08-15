//! virtio-net device: handshake, RX post, TX, and RX classify (D-0038, D-0040).
//!
//! Owns the feature negotiation, QueueReady, RX posting, the first TX
//! (gratuitous ARP), the first RX (classify EtherType, re-post, no reply),
//! MAC print, and `dump` / stall observability. The rings themselves stay
//! in `virtq`. `FEATURES_OK` readback is the one place the handshake can
//! tell us the device rejected our feature set. Without this module the
//! NIC never leaves reset and nothing hits the wire.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::csr;
use crate::print;
use crate::println;
use crate::virtio;
use crate::virtq;
use core::arch::asm;

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
/// How long `wait_rx_arp` spins for slirp's request (~2 s).
const RX_WAIT_TICKS: usize = 20 * STALL_TICKS;

/// virtio-net header with `VIRTIO_F_VERSION_1` and without `MRG_RXBUF`:
/// 10-byte legacy hdr plus `num_buffers`. Virtio 1.2 §5.1.6.1. We zero
/// every field: no CSUM offload, no GSO, TX does not use `num_buffers`.
const VNET_HDR: usize = 12;
/// Ethernet destination + source + EtherType.
const ETH_HDR: usize = 14;
/// EtherType ARP.
const ETH_TYPE_ARP: u16 = 0x0806;
/// Ethernet header (14) + ARP (28). Padded to 60 before FCS.
const GARP: usize = 42;
/// Minimum Ethernet payload without FCS.
const ETH_MIN: usize = 60;
const TX_LEN: u32 = (VNET_HDR + ETH_MIN) as u32;

/// Guest address. D-0042.
const IP: [u8; 4] = [10, 0, 2, 15];
const ETH_BCAST: [u8; 6] = [0xff; 6];

static mut MAC: [u8; 6] = [0; 6];

static mut RX_POSTED: u32 = 0;
static mut RX_COMPLETED: u32 = 0;
static mut RX_SEEN: u16 = 0;
static mut RX_DROP_SHORT: u32 = 0;
static mut RX_DROP_OTHER: u32 = 0;
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
    let d_short = unsafe { RX_DROP_SHORT };
    let d_other = unsafe { RX_DROP_OTHER };
    println!(
        "net: dump status={st:#x} isr={isr:#x} rx avail={rx_a} used={rx_u} posted={rx_p} completed={rx_c} tx avail={tx_a} used={tx_u} posted={tx_p} completed={tx_c} drop_short={d_short} drop_other={d_other}"
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
    unsafe { MAC = mac };

    dump();
    poll_stall();
    // RX before GARP: slirp learns our MAC from the GARP and will not
    // ARP. The T3.5 trigger is that ARP, so wait for it first.
    wait_rx_arp(base);
    tx_gratuitous_arp(base);
}

/// Build a 12-byte zero virtio-net header plus a 60-byte Ethernet frame
/// (42-byte GARP padded) in TX desc 0, publish, notify, poll used.idx.
fn tx_gratuitous_arp(base: usize) {
    let mac = unsafe { MAC };
    {
        let buf = virtq::tx_buf(0);
        write_garp(buf, &mac);
    }
    virtq::post_tx(0, TX_LEN);
    unsafe { TX_POSTED = TX_POSTED.wrapping_add(1) };
    unsafe {
        STALL_ARMED_TIME = csr::time::read();
        STALL_USED_AT_ARM = virtq::tx_used_idx();
        STALL_DUMPED = false;
    }
    println!("virtio-net: TX GARP posted=1 len={TX_LEN} (hdr {VNET_HDR} + eth {ETH_MIN})");
    virtq::notify(base, virtq::Q_TX);

    let t0 = csr::time::read();
    let seen: u16 = 0;
    loop {
        if let Some((_next, id, len)) = virtq::take_tx_used(seen) {
            unsafe { TX_COMPLETED = TX_COMPLETED.wrapping_add(1) };
            println!("virtio-net: TX GARP completed=1 id={id} len={len}");
            break;
        }
        poll_stall();
        if csr::time::read().wrapping_sub(t0) >= STALL_TICKS {
            println!("virtio-net: TX GARP used.idx unmoved after ~100ms");
            dump();
            break;
        }
    }
    dump();
}

/// Poll the RX used ring until one frame classifies as ARP, or ~2 s.
/// Consumed buffers are re-posted (never freed). No reply — T3.6.
fn wait_rx_arp(base: usize) {
    let t0 = csr::time::read();
    loop {
        if poll_rx(base) {
            dump();
            return;
        }
        poll_stall();
        if csr::time::read().wrapping_sub(t0) >= RX_WAIT_TICKS {
            println!("virtio-net: RX no ARP after ~2s");
            dump();
            return;
        }
        // Timer tick wakes us; do not busy-spin the host for 2 s.
        unsafe { asm!("wfi") };
    }
}

/// Take one used RX entry if `used.idx` moved, classify, re-post.
/// Returns true iff that frame was ARP.
fn poll_rx(base: usize) -> bool {
    let seen = unsafe { RX_SEEN };
    let Some((next, id, used_len)) = virtq::take_rx_used(seen) else {
        return false;
    };
    if id as usize >= virtq::RX_BUFS {
        panic!("virtio-net: RX used id={id} >= {}", virtq::RX_BUFS);
    }
    unsafe {
        RX_SEEN = next;
        RX_COMPLETED = RX_COMPLETED.wrapping_add(1);
    }
    let got_arp = classify_rx(id as usize, used_len);
    virtq::repost_rx(id as usize);
    unsafe { RX_POSTED = RX_POSTED.wrapping_add(1) };
    let posted = unsafe { RX_POSTED };
    let completed = unsafe { RX_COMPLETED };
    let inflight = posted.wrapping_sub(completed);
    if inflight != virtq::RX_BUFS as u32 {
        panic!(
            "virtio-net: RX inflight {inflight} want {} (posted={posted} completed={completed}); a leaked buffer starves the device",
            virtq::RX_BUFS
        );
    }
    virtq::notify(base, virtq::Q_RX);
    got_arp
}

/// Strip the 12-byte virtio-net header, then classify on EtherType.
/// `used.elem.len` includes the header (unlike TX, where it was 0).
fn classify_rx(desc_i: usize, used_len: u32) -> bool {
    let buf = virtq::rx_buf(desc_i);
    let n = used_len as usize;
    if n < VNET_HDR + ETH_HDR || n > buf.len() {
        unsafe { RX_DROP_SHORT = RX_DROP_SHORT.wrapping_add(1) };
        println!("virtio-net: RX drop short used.len={used_len}");
        return false;
    }
    // Device wrote [0, used_len): header then Ethernet. Do not parse
    // the header; we declined CSUM/GSO/MRG_RXBUF.
    let frame = &buf[VNET_HDR..n];
    let etype = u16::from_be_bytes([frame[12], frame[13]]);
    if etype != ETH_TYPE_ARP {
        unsafe { RX_DROP_OTHER = RX_DROP_OTHER.wrapping_add(1) };
        println!(
            "virtio-net: RX drop ethertype={etype:#06x} used.len={used_len} frame.len={}",
            frame.len()
        );
        return false;
    }
    println!(
        "virtio-net: RX arp used.len={used_len} hdr={VNET_HDR} frame.len={}",
        frame.len()
    );
    print_hex(frame);
    true
}

/// Zero the virtio-net header, then Ethernet+ARP (42 bytes) padded to 60.
/// One descriptor, no chain: hdr and frame are contiguous in `buf`.
fn write_garp(buf: &mut [u8], mac: &[u8; 6]) {
    if buf.len() < TX_LEN as usize {
        panic!("virtio-net: TX buf {} < {TX_LEN}", buf.len());
    }
    for b in buf.iter_mut().take(TX_LEN as usize) {
        *b = 0;
    }
    // Header [0, 12) already zero: flags, gso_type, hdr_len, gso_size,
    // csum_start, csum_offset, num_buffers. Device is not asked to
    // checksum or segment; we declined those features.
    let f = &mut buf[VNET_HDR..];
    f[0..6].copy_from_slice(&ETH_BCAST);
    f[6..12].copy_from_slice(mac);
    f[12..14].copy_from_slice(&0x0806u16.to_be_bytes()); // EtherType ARP
    f[14..16].copy_from_slice(&1u16.to_be_bytes()); // htype Ethernet
    f[16..18].copy_from_slice(&0x0800u16.to_be_bytes()); // ptype IPv4
    f[18] = 6;
    f[19] = 4;
    f[20..22].copy_from_slice(&1u16.to_be_bytes()); // ARP request
    f[22..28].copy_from_slice(mac); // sha
    f[28..32].copy_from_slice(&IP); // spa 10.0.2.15
    // tha [32, 38) stays zero
    f[38..42].copy_from_slice(&IP); // tpa 10.0.2.15
    // [42, 60) already zero: pad to Ethernet minimum.
    println!("virtio-net: GARP 42 bytes (padded to 60):");
    print_hex(&f[0..GARP]);
}

fn print_hex(bytes: &[u8]) {
    let mut i = 0;
    while i < bytes.len() {
        if i % 16 == 0 {
            if i > 0 {
                println!();
            }
            print!("  ");
        } else if i % 8 == 0 {
            print!(" ");
        }
        print!("{:02x} ", bytes[i]);
        i += 1;
    }
    println!();
}
