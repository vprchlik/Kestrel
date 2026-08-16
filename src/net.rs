//! virtio-net device: handshake, RX/TX, ARP, IPv4, ICMP, UDP, TCP (D-0038, D-0040, D-0041).
//!
//! Owns the feature negotiation, QueueReady, RX posting, TX (ARP
//! request for the gateway, GARP, ARP replies, ICMP echo, UDP), MAC
//! print, and `dump` / stall / DEVICE_NEEDS_RESET observability.
//! Parse lives in `arp`/`ipv4`/`icmp`/`udp`. The rings stay in `virtq`.
//! UDP payloads wait for `recv`; `send` transmits. Without this module
//! the NIC never leaves reset and the app has nothing to poll.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::arp;
#[cfg(not(feature = "fast-boot"))]
use crate::checksum;
use crate::csr;
use crate::icmp;
use crate::ipv4;
use crate::print;
use crate::println;
use crate::tcp;
use crate::timer;
use crate::udp;
use crate::virtio;
use crate::virtq;
#[cfg(feature = "net-init-selftest")]
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
/// DEVICE_NEEDS_RESET. §2.1 bit 6. Once set, the device has stopped
/// cooperating; polling `used.idx` will not recover.
const NEEDS_RESET: u32 = 64;
/// FAILED. §2.1.
const FAILED: u32 = 128;

/// `VIRTIO_NET_F_MAC` — bit 5, word 0. §5.1.3.
const F_MAC: u32 = 1 << 5;
/// `VIRTIO_F_VERSION_1` — bit 32, so bit 0 of word 1. §6.
const F_VERSION_1: u32 = 1 << 0;

/// 100 ms at the 10 MHz `rdtime` timebase (`timer::PERIOD` is 10 ms).
const STALL_TICKS: usize = 1_000_000;
/// How long `wait_gateway_arp` spins for slirp's reply (~2 s).
const RX_WAIT_TICKS: usize = 20 * STALL_TICKS;

/// virtio-net header with `VIRTIO_F_VERSION_1` and without `MRG_RXBUF`:
/// 10-byte legacy hdr plus `num_buffers`. Virtio 1.2 §5.1.6.1. We zero
/// every field: no CSUM offload, no GSO, TX does not use `num_buffers`.
const VNET_HDR: usize = 12;
/// Ethernet destination + source + EtherType.
const ETH_HDR: usize = 14;
/// EtherType ARP.
const ETH_TYPE_ARP: u16 = 0x0806;
/// EtherType IPv4.
const ETH_TYPE_IPV4: u16 = 0x0800;
/// Ethernet header (14) + ARP (28). Padded to 60 before FCS.
const GARP: usize = 42;
/// Minimum Ethernet payload without FCS.
const ETH_MIN: usize = 60;
const TX_LEN: u32 = (VNET_HDR + ETH_MIN) as u32;

/// Guest address. D-0042.
const IP: [u8; 4] = [10, 0, 2, 15];
/// slirp gateway. D-0042 / D-0047.
const GW: [u8; 4] = [10, 0, 2, 2];
const ETH_BCAST: [u8; 6] = [0xff; 6];
/// ICMP echo identifier/sequence for the guest-initiated ping.
const PING_ID: u16 = 1;
const PING_SEQ: u16 = 1;
const PING_DATA: &[u8] = b"whimbrel";

static mut MAC: [u8; 6] = [0; 6];

static mut RX_POSTED: u32 = 0;
static mut RX_COMPLETED: u32 = 0;
static mut RX_SEEN: u16 = 0;
static mut RX_DROP_SHORT: u32 = 0;
static mut RX_DROP_OTHER: u32 = 0;
static mut TX_POSTED: u32 = 0;
static mut TX_COMPLETED: u32 = 0;
static mut TX_SEEN: u16 = 0;
static mut STALL_ARMED_TIME: usize = 0;
static mut STALL_USED_AT_ARM: u16 = 0;
static mut STALL_DUMPED: bool = false;
static mut PING_TX: usize = 0;
static mut PING_RX: usize = 0;
static mut PING_DONE: bool = false;

/// One unread UDP payload (D-0040). `recv` copies this into the user
/// buffer; a second datagram before that copy is dropped, not queued.
/// Cap on one UDP payload queued for `recv`. Public so the kernel can
/// `const`-assert the app buffer against it (D-0056 / finding 36).
pub const UDP_PAYLOAD_MAX: usize = 1472;
static mut UDP_PENDING: bool = false;
static mut UDP_LEN: usize = 0;
static mut UDP_SRC_IP: [u8; 4] = [0; 4];
static mut UDP_SRC_PORT: u16 = 0;
static mut UDP_BUF: [u8; UDP_PAYLOAD_MAX] = [0; UDP_PAYLOAD_MAX];
static mut UDP_PEER: bool = false;

fn status(base: usize) -> u32 {
    virtio::read32(base, virtio::OFF_STATUS)
}

fn write_status(base: usize, st: u32) -> u32 {
    virtio::write32(base, virtio::OFF_STATUS, st);
    let got = status(base);
    if got & FAILED != 0 {
        panic!("virtio-net: FAILED status={got:#x} after write {st:#x}");
    }
    if got & NEEDS_RESET != 0 {
        panic!("virtio-net: DEVICE_NEEDS_RESET status={got:#x} after write {st:#x}");
    }
    got
}

fn require_device(base: usize) {
    let st = status(base);
    if st & NEEDS_RESET != 0 {
        dump();
        panic!("virtio-net: DEVICE_NEEDS_RESET status={st:#x}; device has stopped cooperating");
    }
    if st & FAILED != 0 {
        dump();
        panic!("virtio-net: FAILED status={st:#x}");
    }
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
    #[cfg(not(feature = "fast-boot"))]
    {
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
        let tag = if st & NEEDS_RESET != 0 {
            " DEVICE_NEEDS_RESET"
        } else {
            ""
        };
        println!(
            "net: dump{tag} status={st:#x} isr={isr:#x} rx avail={rx_a} used={rx_u} posted={rx_p} completed={rx_c} tx avail={tx_a} used={tx_u} posted={tx_p} completed={tx_c} drop_short={d_short} drop_other={d_other} arp_drop short={} htype={} ptype={} hlen={} plen={} op={} tpa={} ip_drop short={} ver={} ihl={} csum={} frag={} dst={} proto={} icmp_drop short={} csum={} type={} udp_drop short={} len={} csum={} port={} tcp_drop short={} doff={} csum={} opt={} port={} noarp={} busy={} unexpected={} established={} rexmit={}",
            unsafe { arp::DROP_SHORT },
            unsafe { arp::DROP_HTYPE },
            unsafe { arp::DROP_PTYPE },
            unsafe { arp::DROP_HLEN },
            unsafe { arp::DROP_PLEN },
            unsafe { arp::DROP_OP },
            unsafe { arp::DROP_TPA },
            unsafe { ipv4::DROP_SHORT },
            unsafe { ipv4::DROP_VER },
            unsafe { ipv4::DROP_IHL },
            unsafe { ipv4::DROP_CSUM },
            unsafe { ipv4::DROP_FRAG },
            unsafe { ipv4::DROP_DST },
            unsafe { ipv4::DROP_PROTO },
            unsafe { icmp::DROP_SHORT },
            unsafe { icmp::DROP_CSUM },
            unsafe { icmp::DROP_TYPE },
            unsafe { udp::DROP_SHORT },
            unsafe { udp::DROP_LEN },
            unsafe { udp::DROP_CSUM },
            unsafe { udp::DROP_PORT },
            unsafe { tcp::DROP_SHORT },
            unsafe { tcp::DROP_DOFF },
            unsafe { tcp::DROP_CSUM },
            unsafe { tcp::DROP_OPT },
            unsafe { tcp::DROP_PORT },
            unsafe { tcp::DROP_NOARP },
            unsafe { tcp::DROP_BUSY },
            unsafe { tcp::DROP_UNEXPECTED },
            unsafe { tcp::ESTABLISHED },
            tcp::rexmit(),
        );
    }
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
    println!("net: stall tx posted={posted} used.idx={used} unmoved for ~100ms");
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

    let _got = write_status(base, ACKNOWLEDGE);
    println!("virtio-net: ACKNOWLEDGE status={_got:#x}");
    let _got = write_status(base, ACKNOWLEDGE | DRIVER);
    println!("virtio-net: DRIVER status={_got:#x}");

    let (dev0, dev1) = read_device_features(base);
    println!("virtio-net: device features word0={dev0:#x} word1={dev1:#x}");
    println!(
        "virtio-net: offered MAC={} MRG_RXBUF={} STATUS={} EVENT_IDX={} VERSION_1={}",
        dev0 & F_MAC != 0,
        dev0 & (1 << 15) != 0,
        dev0 & (1 << 16) != 0,
        dev0 & (1 << 29) != 0,
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
    crate::phase::stamp(crate::phase::DRIVER_OK);

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

    #[cfg(not(feature = "fast-boot"))]
    {
        arp::wrap_selftest();
        checksum::selftest();
        icmp::reply_selftest();
        udp::selftest();
        tcp::selftest();
    }
    dump();
    poll_stall();
    wait_gateway_arp(base);
    tx_gratuitous_arp(base);
    ping_gateway(base);
    #[cfg(feature = "net-init-selftest")]
    wait_tcp_handshake(base);
    crate::phase::stamp(crate::phase::LISTEN);
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
    arm_tx_stall();
    println!("virtio-net: TX GARP posted=1 len={TX_LEN} (hdr {VNET_HDR} + eth {ETH_MIN})");
    virtq::notify(base, virtq::Q_TX);
    wait_tx(base, "GARP");
    dump();
}

fn arm_tx_stall() {
    unsafe {
        STALL_ARMED_TIME = csr::time::read();
        STALL_USED_AT_ARM = virtq::tx_used_idx();
        STALL_DUMPED = false;
    }
}

/// Wait until `tx used.idx` advances past `TX_SEEN`, or ~100 ms.
fn wait_tx(base: usize, why: &str) {
    require_device(base);
    let t0 = csr::time::read();
    loop {
        require_device(base);
        let seen = unsafe { TX_SEEN };
        if let Some((next, _id, _len)) = virtq::take_tx_used(seen) {
            unsafe {
                TX_SEEN = next;
                TX_COMPLETED = TX_COMPLETED.wrapping_add(1);
            }
            println!(
                "virtio-net: TX {why} completed={} id={_id} len={_len}",
                unsafe { TX_COMPLETED }
            );
            return;
        }
        poll_stall();
        if csr::time::read().wrapping_sub(t0) >= STALL_TICKS {
            println!("virtio-net: TX {why} used.idx unmoved after ~100ms");
            dump();
            panic!("virtio-net: TX {why} did not complete; refusing to reuse the descriptor");
        }
    }
}

/// ARP request for 10.0.2.2, then drain RX until the cache holds it
/// (D-0054). Standalone boot does not wait to be asked.
fn wait_gateway_arp(base: usize) {
    tx_arp_request(base);
    let t0 = csr::time::read();
    loop {
        require_device(base);
        let _ = poll_rx(base);
        if arp::lookup(GW).is_some() {
            println!("virtio-net: gateway 10.0.2.2 MAC learned");
            dump();
            return;
        }
        poll_stall();
        if csr::time::read().wrapping_sub(t0) >= RX_WAIT_TICKS {
            println!("virtio-net: RX no ARP reply from 10.0.2.2 after ~2s");
            dump();
            panic!("virtio-net: no ARP reply for gateway 10.0.2.2");
        }
        // Spin, don't wfi (D-0056 / finding 12). Only the 10 ms timer
        // could wake a wfi here, which quantized ARP latency to the tick.
        // Finding 13 corollary: the 2 s timeout sits after the wfi in
        // this loop, so removing timer arming without this change hangs
        // forever — the first wfi with nothing pending never returns to
        // the timeout check.
    }
}

fn tx_arp_request(base: usize) {
    let mac = unsafe { MAC };
    {
        let buf = virtq::tx_buf(0);
        for b in buf.iter_mut().take(TX_LEN as usize) {
            *b = 0;
        }
        arp::write_request(&mut buf[VNET_HDR..], &mac, &IP, &GW);
    }
    virtq::post_tx(0, TX_LEN);
    unsafe { TX_POSTED = TX_POSTED.wrapping_add(1) };
    arm_tx_stall();
    println!("virtio-net: TX ARP request for 10.0.2.2");
    virtq::notify(base, virtq::Q_TX);
    wait_tx(base, "ARP request");
}

/// Drain every used RX entry. Returns true iff we transmitted an ARP reply.
fn poll_rx(base: usize) -> bool {
    require_device(base);
    let mut took = 0u32;
    let mut replied = false;
    loop {
        let seen = unsafe { RX_SEEN };
        let Some((next, id, used_len)) = virtq::take_rx_used(seen) else {
            break;
        };
        if id as usize >= virtq::RX_BUFS {
            panic!("virtio-net: RX used id={id} >= {}", virtq::RX_BUFS);
        }
        unsafe {
            RX_SEEN = next;
            RX_COMPLETED = RX_COMPLETED.wrapping_add(1);
        }
        if classify_rx(base, id as usize, used_len) {
            replied = true;
        }
        virtq::repost_rx(id as usize);
        unsafe { RX_POSTED = RX_POSTED.wrapping_add(1) };
        took += 1;
    }
    if took != 0 {
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
    }
    replied
}

/// Strip the 12-byte virtio-net header, classify EtherType, maybe reply.
/// Returns true iff we posted an ARP reply (and waited for TX complete).
fn classify_rx(base: usize, desc_i: usize, used_len: u32) -> bool {
    crate::phase::stamp(crate::phase::FIRST_RX);
    let buf = virtq::rx_buf(desc_i);
    let n = used_len as usize;
    if n < VNET_HDR + ETH_HDR || n > buf.len() {
        unsafe { RX_DROP_SHORT = RX_DROP_SHORT.wrapping_add(1) };
        println!("virtio-net: RX drop short used.len={used_len}");
        return false;
    }
    let frame = &buf[VNET_HDR..n];
    let etype = u16::from_be_bytes([frame[12], frame[13]]);
    if etype == ETH_TYPE_IPV4 {
        classify_ipv4(base, frame);
        return false;
    }
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
    match arp::process(frame, &IP) {
        Some(arp::Event::Request(req)) => {
            tx_arp_reply(base, &req);
            true
        }
        Some(arp::Event::Reply) => false,
        None => false,
    }
}

fn tx_arp_reply(base: usize, req: &arp::RequestForUs) {
    let mac = unsafe { MAC };
    {
        let buf = virtq::tx_buf(0);
        for b in buf.iter_mut().take(TX_LEN as usize) {
            *b = 0;
        }
        arp::write_reply(&mut buf[VNET_HDR..], &mac, &IP, req);
    }
    virtq::post_tx(0, TX_LEN);
    unsafe { TX_POSTED = TX_POSTED.wrapping_add(1) };
    arm_tx_stall();
    println!(
        "virtio-net: TX ARP reply to {}.{}.{}.{}",
        req.spa[0], req.spa[1], req.spa[2], req.spa[3]
    );
    virtq::notify(base, virtq::Q_TX);
    wait_tx(base, "ARP reply");
}

fn gateway_mac() -> [u8; 6] {
    match arp::lookup(GW) {
        Some(m) => m,
        None => panic!("net: no MAC for gateway 10.0.2.2; TX does not ARP-and-queue (D-0047)"),
    }
}

fn classify_ipv4(base: usize, frame: &[u8]) {
    let Some(d) = ipv4::parse(frame, &IP) else {
        return;
    };
    if d.proto == ipv4::PROTO_UDP {
        classify_udp(base, &d);
        return;
    }
    if d.proto == ipv4::PROTO_TCP {
        classify_tcp(base, &d);
        return;
    }
    match icmp::parse(d.payload) {
        Some(icmp::Msg::EchoReq { id: _id, seq: _seq, raw }) => {
            println!("virtio-net: RX icmp echo-req id={_id} seq={_seq}");
            tx_icmp_echo_reply(base, d.src, raw);
        }
        Some(icmp::Msg::EchoReply { id, seq }) => {
            if id == PING_ID && seq == PING_SEQ && !unsafe { PING_DONE } {
                unsafe {
                    PING_RX = csr::time::read();
                    PING_DONE = true;
                }
                println!("virtio-net: RX icmp echo-reply id={id} seq={seq}");
            } else {
                println!("virtio-net: RX icmp echo-reply unmatched id={id} seq={seq}");
            }
        }
        None => {}
    }
}

fn classify_tcp(base: usize, d: &ipv4::Datagram<'_>) {
    // D-0047: TX does not ARP-and-queue. A SYN before the cache holds
    // 10.0.2.2 is a drop, not a panic (D-0040 / D-0052).
    if arp::lookup(GW).is_none() {
        tcp::note_noarp();
        return;
    }
    tx_tcp(base, tcp::handle(d.payload, &d.src, &IP));
}

fn pump_tcp(base: usize) {
    tx_tcp(base, tcp::check_rto());
}

fn tx_tcp(base: usize, out: tcp::Out) {
    match out {
        tcp::Out::None => {}
        tcp::Out::SynAck {
            dst_ip,
            dst_port,
            seq,
            ack,
        } => tx_tcp_synack(base, dst_ip, dst_port, seq, ack),
        tcp::Out::Seg {
            dst_ip,
            dst_port,
            seq,
            ack,
            flags,
            data_len,
        } => tx_tcp_seg(base, dst_ip, dst_port, seq, ack, flags, data_len),
    }
}

fn tx_tcp_synack(base: usize, dst_ip: [u8; 4], dst_port: u16, seq: u32, ack: u32) {
    let Some(gw) = arp::lookup(GW) else {
        tcp::note_noarp();
        return;
    };
    let mac = unsafe { MAC };
    let tcp_len = 24;
    let ip_tot = 20 + tcp_len;
    let eth_len = core::cmp::max(ETH_HDR + ip_tot, ETH_MIN);
    let post = (VNET_HDR + eth_len) as u32;
    {
        let buf = virtq::tx_buf(0);
        if buf.len() < post as usize {
            panic!("virtio-net: TX buf {} < {post}", buf.len());
        }
        for b in buf.iter_mut().take(post as usize) {
            *b = 0;
        }
        let f = &mut buf[VNET_HDR..];
        f[0..6].copy_from_slice(&gw);
        f[6..12].copy_from_slice(&mac);
        f[12..14].copy_from_slice(&ETH_TYPE_IPV4.to_be_bytes());
        ipv4::write_header(
            &mut f[ETH_HDR..],
            ip_tot as u16,
            ipv4::PROTO_TCP,
            &IP,
            &dst_ip,
        );
        tcp::write_synack(
            &mut f[ETH_HDR + 20..],
            tcp::LISTEN_PORT,
            dst_port,
            seq,
            ack,
            &IP,
            &dst_ip,
        );
    }
    virtq::post_tx(0, post);
    unsafe { TX_POSTED = TX_POSTED.wrapping_add(1) };
    arm_tx_stall();
    println!("virtio-net: TX TCP SYN/ACK seq={seq} ack={ack} dport={dst_port}");
    virtq::notify(base, virtq::Q_TX);
    wait_tx(base, "TCP SYN/ACK");
}

fn tx_tcp_seg(
    base: usize,
    dst_ip: [u8; 4],
    dst_port: u16,
    seq: u32,
    ack: u32,
    flags: u8,
    data_len: usize,
) {
    let Some(gw) = arp::lookup(GW) else {
        tcp::note_noarp();
        return;
    };
    let mac = unsafe { MAC };
    let payload = if data_len == 0 {
        &[][..]
    } else {
        let u = tcp::unacked_bytes();
        if data_len > u.len() {
            panic!("virtio-net: TCP data_len {data_len} > unacked {}", u.len());
        }
        &u[..data_len]
    };
    let tcp_len = 20 + payload.len();
    let ip_tot = 20 + tcp_len;
    let eth_len = core::cmp::max(ETH_HDR + ip_tot, ETH_MIN);
    let post = (VNET_HDR + eth_len) as u32;
    {
        let buf = virtq::tx_buf(0);
        if buf.len() < post as usize {
            panic!("virtio-net: TX buf {} < {post}", buf.len());
        }
        for b in buf.iter_mut().take(post as usize) {
            *b = 0;
        }
        let f = &mut buf[VNET_HDR..];
        f[0..6].copy_from_slice(&gw);
        f[6..12].copy_from_slice(&mac);
        f[12..14].copy_from_slice(&ETH_TYPE_IPV4.to_be_bytes());
        ipv4::write_header(
            &mut f[ETH_HDR..],
            ip_tot as u16,
            ipv4::PROTO_TCP,
            &IP,
            &dst_ip,
        );
        tcp::write_seg(
            &mut f[ETH_HDR + 20..],
            tcp::LISTEN_PORT,
            dst_port,
            seq,
            ack,
            flags,
            payload,
            &IP,
            &dst_ip,
        );
    }
    virtq::post_tx(0, post);
    let first_http = data_len > 0 && crate::phase::get(crate::phase::E3G) == 0;
    if first_http {
        // Publish-time (D-0043 / D-0056). The doorbell store is the next
        // stamp: under TCG it runs the device model in this thread.
        crate::phase::stamp(crate::phase::E3G);
    }
    unsafe { TX_POSTED = TX_POSTED.wrapping_add(1) };
    arm_tx_stall();
    println!("virtio-net: TX TCP seg seq={seq} ack={ack} flags={flags:#04x} dlen={data_len}");
    virtq::notify(base, virtq::Q_TX);
    if first_http {
        crate::phase::stamp(crate::phase::E3G_DOORBELL);
    }
    wait_tx(base, "TCP seg");
    if first_http {
        crate::phase::print_after_response();
        crate::println_always!("M3 UNIKERNEL OK");
    }
}

fn classify_udp(_base: usize, d: &ipv4::Datagram<'_>) {
    let Some(echo) = udp::parse(d.payload, &d.src, &IP) else {
        return;
    };
    let payload = echo.payload();
    println!(
        "virtio-net: RX udp echo sport={} dport={} len={}",
        echo.src_port,
        udp::ECHO_PORT,
        echo.raw.len()
    );
    if payload.len() > UDP_PAYLOAD_MAX {
        println!(
            "virtio-net: RX udp payload {} > {UDP_PAYLOAD_MAX}; dropped",
            payload.len()
        );
        return;
    }
    if unsafe { UDP_PENDING } {
        println!("virtio-net: RX udp dropped (unread payload still pending)");
        return;
    }
    unsafe {
        core::ptr::copy_nonoverlapping(
            payload.as_ptr(),
            core::ptr::addr_of_mut!(UDP_BUF) as *mut u8,
            payload.len(),
        );
        UDP_LEN = payload.len();
        UDP_SRC_IP = d.src;
        UDP_SRC_PORT = echo.src_port;
        UDP_PENDING = true;
        UDP_PEER = true;
    }
}

/// Drain the RX used ring. Every `recv` syscall does this — that is
/// what advances the stack (D-0040). Never called from the trap path.
pub fn poll() {
    let base = virtio::net_base();
    let _ = poll_rx(base);
    pump_tcp(base);
}

pub fn tcp_pending() -> Option<&'static [u8]> {
    tcp::pending()
}

pub fn tcp_consume() {
    tcp::consume();
}

pub fn tcp_eof() -> bool {
    tcp::take_eof()
}

pub fn tcp_send(payload: &[u8], flags: usize) -> bool {
    let fin = flags & 1 != 0;
    match tcp::app_send(payload, fin) {
        tcp::Out::None => false,
        out => {
            tx_tcp(virtio::net_base(), out);
            true
        }
    }
}

/// Pending UDP payload, if `poll` has classified one since the last take.
pub fn udp_pending() -> Option<&'static [u8]> {
    if unsafe { UDP_PENDING } {
        Some(unsafe {
            core::slice::from_raw_parts(core::ptr::addr_of!(UDP_BUF) as *const u8, UDP_LEN)
        })
    } else {
        None
    }
}

pub fn udp_consume() {
    unsafe {
        UDP_PENDING = false;
        UDP_LEN = 0;
    }
}

/// Transmit `payload` to the last UDP peer. FIN is ignored on UDP
/// (D-0051). Returns false if nothing has been received yet.
pub fn udp_send(payload: &[u8], _flags: usize) -> bool {
    if !unsafe { UDP_PEER } {
        return false;
    }
    let dst_ip = unsafe { UDP_SRC_IP };
    let dst_port = unsafe { UDP_SRC_PORT };
    tx_udp(payload, dst_ip, dst_port);
    true
}

fn tx_udp(payload: &[u8], dst_ip: [u8; 4], dst_port: u16) {
    let gw = gateway_mac();
    let mac = unsafe { MAC };
    let udp_len = 8 + payload.len();
    let ip_tot = 20 + udp_len;
    let eth_len = core::cmp::max(ETH_HDR + ip_tot, ETH_MIN);
    let post = (VNET_HDR + eth_len) as u32;
    {
        let buf = virtq::tx_buf(0);
        if buf.len() < post as usize {
            panic!("virtio-net: TX buf {} < {post}", buf.len());
        }
        for b in buf.iter_mut().take(post as usize) {
            *b = 0;
        }
        let f = &mut buf[VNET_HDR..];
        f[0..6].copy_from_slice(&gw);
        f[6..12].copy_from_slice(&mac);
        f[12..14].copy_from_slice(&ETH_TYPE_IPV4.to_be_bytes());
        ipv4::write_header(
            &mut f[ETH_HDR..],
            ip_tot as u16,
            ipv4::PROTO_UDP,
            &IP,
            &dst_ip,
        );
        udp::write_dgram(
            &mut f[ETH_HDR + 20..],
            udp::ECHO_PORT,
            dst_port,
            payload,
            &IP,
            &dst_ip,
        );
    }
    virtq::post_tx(0, post);
    unsafe { TX_POSTED = TX_POSTED.wrapping_add(1) };
    arm_tx_stall();
    println!(
        "virtio-net: TX UDP echo to {}.{}.{}.{} dport mirrored",
        dst_ip[0], dst_ip[1], dst_ip[2], dst_ip[3]
    );
    virtq::notify(virtio::net_base(), virtq::Q_TX);
    wait_tx(virtio::net_base(), "UDP echo");
}

fn tx_icmp_echo_reply(base: usize, dst_ip: [u8; 4], req: &[u8]) {
    let gw = gateway_mac();
    let mac = unsafe { MAC };
    let icmp_len = req.len();
    let ip_tot = 20 + icmp_len;
    let eth_len = core::cmp::max(ETH_HDR + ip_tot, ETH_MIN);
    let post = (VNET_HDR + eth_len) as u32;
    {
        let buf = virtq::tx_buf(0);
        if buf.len() < post as usize {
            panic!("virtio-net: TX buf {} < {post}", buf.len());
        }
        for b in buf.iter_mut().take(post as usize) {
            *b = 0;
        }
        let f = &mut buf[VNET_HDR..];
        f[0..6].copy_from_slice(&gw);
        f[6..12].copy_from_slice(&mac);
        f[12..14].copy_from_slice(&ETH_TYPE_IPV4.to_be_bytes());
        ipv4::write_header(
            &mut f[ETH_HDR..],
            ip_tot as u16,
            ipv4::PROTO_ICMP,
            &IP,
            &dst_ip,
        );
        icmp::write_echo_reply(&mut f[ETH_HDR + 20..], req);
    }
    virtq::post_tx(0, post);
    unsafe { TX_POSTED = TX_POSTED.wrapping_add(1) };
    arm_tx_stall();
    println!(
        "virtio-net: TX ICMP echo reply to {}.{}.{}.{}",
        dst_ip[0], dst_ip[1], dst_ip[2], dst_ip[3]
    );
    virtq::notify(base, virtq::Q_TX);
    wait_tx(base, "ICMP echo reply");
}

/// Guest-initiated ping of 10.0.2.2 (D-0048). Ethernet dest is the
/// gateway MAC from the ARP cache (D-0047).
fn ping_gateway(base: usize) {
    let gw = gateway_mac();
    let mac = unsafe { MAC };
    let icmp_len = 8 + PING_DATA.len();
    let ip_tot = 20 + icmp_len;
    let eth_len = core::cmp::max(ETH_HDR + ip_tot, ETH_MIN);
    let post = (VNET_HDR + eth_len) as u32;
    {
        let buf = virtq::tx_buf(0);
        if buf.len() < post as usize {
            panic!("virtio-net: TX buf {} < {post}", buf.len());
        }
        for b in buf.iter_mut().take(post as usize) {
            *b = 0;
        }
        let f = &mut buf[VNET_HDR..];
        f[0..6].copy_from_slice(&gw);
        f[6..12].copy_from_slice(&mac);
        f[12..14].copy_from_slice(&ETH_TYPE_IPV4.to_be_bytes());
        ipv4::write_header(&mut f[ETH_HDR..], ip_tot as u16, ipv4::PROTO_ICMP, &IP, &GW);
        icmp::write_echo_req(&mut f[ETH_HDR + 20..], PING_ID, PING_SEQ, PING_DATA);
    }
    virtq::post_tx(0, post);
    unsafe {
        TX_POSTED = TX_POSTED.wrapping_add(1);
        PING_DONE = false;
        PING_TX = csr::time::read();
    }
    arm_tx_stall();
    println!("virtio-net: TX ICMP echo id={PING_ID} seq={PING_SEQ} to 10.0.2.2");
    virtq::notify(base, virtq::Q_TX);
    wait_tx(base, "ICMP echo");
    wait_ping_reply(base);
}

fn wait_ping_reply(base: usize) {
    let t0 = csr::time::read();
    loop {
        require_device(base);
        let _ = poll_rx(base);
        pump_tcp(base);
        if unsafe { PING_DONE } {
            print_ping_rtt();
            dump();
            return;
        }
        poll_stall();
        if csr::time::read().wrapping_sub(t0) >= RX_WAIT_TICKS {
            if unsafe { PING_DONE } {
                print_ping_rtt();
                dump();
                return;
            }
            println!("virtio-net: RX no ICMP echo reply after ~2s");
            dump();
            panic!("virtio-net: no echo reply from 10.0.2.2");
        }
        // Spin, don't wfi (D-0056 / finding 12). Same as wait_gateway_arp:
        // the tick was the only wake. Finding 13 corollary: the 2 s
        // timeout sits after the wfi, so removing timer arming without
        // this change hangs forever.
    }
}

/// Hostfwd connect after the gateway MAC is cached (D-0046, retargeted).
/// Stay in `poll_rx` until ESTABLISHED and LISTEN restore so this
/// image does not shut down during ping (D-0054).
#[cfg(feature = "net-init-selftest")]
fn wait_tcp_handshake(base: usize) {
    let t0 = csr::time::read();
    let mut saw_est = false;
    loop {
        require_device(base);
        let _ = poll_rx(base);
        pump_tcp(base);
        if tcp::established() > 0 {
            saw_est = true;
        }
        if saw_est && tcp::listening() {
            dump();
            return;
        }
        poll_stall();
        if csr::time::read().wrapping_sub(t0) >= RX_WAIT_TICKS {
            dump();
            if !saw_est {
                panic!("virtio-net: no TCP handshake after ~2s");
            }
            panic!("virtio-net: TCP handshake did not return to LISTEN after ~2s");
        }
        unsafe { asm!("wfi") };
    }
}

/// Stable keys for M4: tx/rx are `rdtime`, ns = ticks * `timer::TICK_NS`.
fn print_ping_rtt() {
    let tx = unsafe { PING_TX };
    let rx = unsafe { PING_RX };
    let ticks = rx.wrapping_sub(tx);
    let _ns = ticks.wrapping_mul(timer::TICK_NS);
    println!(
        "PING RTT dst=10.0.2.2 id={PING_ID} seq={PING_SEQ} tx={tx} rx={rx} ticks={ticks} ns={_ns}"
    );
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
