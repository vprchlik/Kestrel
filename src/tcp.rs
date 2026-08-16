//! TCP: passive open, one data segment, close, one RTO (D-0041, D-0053).
//!
//! Owns the single TCB. Data offset is the high nibble of byte 12 on
//! every segment. In-order payload is queued for `recv`; `send` with
//! FIN is stop-and-wait (one unacked segment, 200 ms `rdtime` RTO from
//! the poll loop, 8 attempts then RST). SYN and FIN each consume a
//! sequence number. Truncated TIME_WAIT logs and returns to LISTEN.
//! A second 4-tuple is dropped, not queued. Without this module curl
//! has no HTTP and close hangs on an off-by-one.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::checksum;
use crate::csr;
use crate::ipv4;
use crate::println;

const HDR_MIN: usize = 20;
pub const LISTEN_PORT: u16 = 80;
const WINDOW: u16 = 8192;
const DEFAULT_MSS: u16 = 536;
const OUR_MSS: u16 = 1460;
const SYNACK_HLEN: usize = 24;
/// 200 ms at the 10 MHz timebase (D-0041).
const RTO_TICKS: usize = 2_000_000;
const RTO_MAX: u8 = 8;
const PAYLOAD_MAX: usize = 512;

const FIN: u8 = 0x01;
const SYN: u8 = 0x02;
const RST: u8 = 0x04;
const PSH: u8 = 0x08;
const ACK: u8 = 0x10;

const OPT_EOL: u8 = 0;
const OPT_NOP: u8 = 1;
const OPT_MSS: u8 = 2;
const OPT_MSS_LEN: u8 = 4;

#[derive(Clone, Copy, PartialEq, Eq)]
enum State {
    Listen,
    SynRcvd,
    Established,
    FinWait1,
    FinWait2,
    CloseWait,
    LastAck,
}

struct Tcb {
    state: State,
    remote_ip: [u8; 4],
    remote_port: u16,
    their_isn: u32,
    rcv_nxt: u32,
    snd_isn: u32,
    snd_una: u32,
    snd_nxt: u32,
    mss: u16,
    rx: [u8; PAYLOAD_MAX],
    rx_len: usize,
    rx_pending: bool,
    unacked: [u8; PAYLOAD_MAX],
    unacked_len: usize,
    unacked_fin: bool,
    inflight: bool,
    deadline: usize,
    tries: u8,
    saw_data: bool,
    app_sent: bool,
    eof: bool,
    peer_fin: bool,
    rexmit: u32,
}

static mut TCB: Tcb = Tcb {
    state: State::Listen,
    remote_ip: [0; 4],
    remote_port: 0,
    their_isn: 0,
    rcv_nxt: 0,
    snd_isn: 0,
    snd_una: 0,
    snd_nxt: 0,
    mss: DEFAULT_MSS,
    rx: [0; PAYLOAD_MAX],
    rx_len: 0,
    rx_pending: false,
    unacked: [0; PAYLOAD_MAX],
    unacked_len: 0,
    unacked_fin: false,
    inflight: false,
    deadline: 0,
    tries: 0,
    saw_data: false,
    app_sent: false,
    eof: false,
    peer_fin: false,
    rexmit: 0,
};

static mut DROP_SHORT: u32 = 0;
static mut DROP_DOFF: u32 = 0;
static mut DROP_CSUM: u32 = 0;
static mut DROP_OPT: u32 = 0;
static mut DROP_PORT: u32 = 0;
static mut DROP_NOARP: u32 = 0;
static mut DROP_BUSY: u32 = 0;
static mut DROP_UNEXPECTED: u32 = 0;
static mut ESTABLISHED: u32 = 0;

pub fn drop_short() -> u32 {
    unsafe { DROP_SHORT }
}
pub fn drop_doff() -> u32 {
    unsafe { DROP_DOFF }
}
pub fn drop_csum() -> u32 {
    unsafe { DROP_CSUM }
}
pub fn drop_opt() -> u32 {
    unsafe { DROP_OPT }
}
pub fn drop_port() -> u32 {
    unsafe { DROP_PORT }
}
pub fn drop_noarp() -> u32 {
    unsafe { DROP_NOARP }
}
pub fn drop_busy() -> u32 {
    unsafe { DROP_BUSY }
}
pub fn drop_unexpected() -> u32 {
    unsafe { DROP_UNEXPECTED }
}
pub fn established() -> u32 {
    unsafe { ESTABLISHED }
}
pub fn rexmit() -> u32 {
    unsafe { TCB.rexmit }
}
pub fn listening() -> bool {
    unsafe { TCB.state == State::Listen }
}

pub fn note_noarp() {
    unsafe { DROP_NOARP = DROP_NOARP.wrapping_add(1) };
    println!("tcp: drop no arp cache for gateway");
}

/// Wire output. `Seg.data_len` bytes come from [`unacked_bytes`].
pub enum Out {
    None,
    SynAck {
        dst_ip: [u8; 4],
        dst_port: u16,
        seq: u32,
        ack: u32,
    },
    Seg {
        dst_ip: [u8; 4],
        dst_port: u16,
        seq: u32,
        ack: u32,
        flags: u8,
        data_len: usize,
    },
}

pub fn unacked_bytes() -> &'static [u8] {
    let t = unsafe { &*core::ptr::addr_of!(TCB) };
    &t.unacked[..t.unacked_len]
}

fn tcb() -> &'static mut Tcb {
    unsafe { &mut *core::ptr::addr_of_mut!(TCB) }
}

fn be16(b: &[u8], off: usize) -> u16 {
    u16::from_be_bytes([b[off], b[off + 1]])
}

fn be32(b: &[u8], off: usize) -> u32 {
    u32::from_be_bytes([b[off], b[off + 1], b[off + 2], b[off + 3]])
}

fn pseudo_header(src: &[u8; 4], dst: &[u8; 4], tcp_len: u16) -> [u8; 12] {
    let mut p = [0u8; 12];
    p[0..4].copy_from_slice(src);
    p[4..8].copy_from_slice(dst);
    p[8] = 0;
    p[9] = ipv4::PROTO_TCP;
    p[10..12].copy_from_slice(&tcp_len.to_be_bytes());
    p
}

fn tcp_sum(src: &[u8; 4], dst: &[u8; 4], tcp: &[u8]) -> u16 {
    let pseudo = pseudo_header(src, dst, tcp.len() as u16);
    checksum::fold(checksum::accumulate(checksum::accumulate(0, &pseudo), tcp))
}

pub fn checksum_tx(src: &[u8; 4], dst: &[u8; 4], tcp: &[u8]) -> u16 {
    !tcp_sum(src, dst, tcp)
}

fn checksum_valid(src: &[u8; 4], dst: &[u8; 4], tcp: &[u8]) -> bool {
    tcp_sum(src, dst, tcp) == 0xffff
}

fn parse_mss(hdr: &[u8], hlen: usize) -> Option<u16> {
    let mut i = HDR_MIN;
    let mut mss = None;
    while i < hlen {
        let kind = hdr[i];
        if kind == OPT_EOL {
            break;
        }
        if kind == OPT_NOP {
            i += 1;
            continue;
        }
        if i + 1 >= hlen {
            return None;
        }
        let len = hdr[i + 1] as usize;
        if len < 2 || i + len > hlen {
            return None;
        }
        if kind == OPT_MSS {
            if len != OPT_MSS_LEN as usize {
                return None;
            }
            mss = Some(u16::from_be_bytes([hdr[i + 2], hdr[i + 3]]));
        }
        i += len;
    }
    Some(mss.unwrap_or(DEFAULT_MSS))
}

pub fn write_synack(
    dst: &mut [u8],
    src_port: u16,
    dst_port: u16,
    seq: u32,
    ack: u32,
    src_ip: &[u8; 4],
    dst_ip: &[u8; 4],
) -> usize {
    if dst.len() < SYNACK_HLEN {
        panic!("tcp: synack buf {} < {SYNACK_HLEN}", dst.len());
    }
    dst[0..2].copy_from_slice(&src_port.to_be_bytes());
    dst[2..4].copy_from_slice(&dst_port.to_be_bytes());
    dst[4..8].copy_from_slice(&seq.to_be_bytes());
    dst[8..12].copy_from_slice(&ack.to_be_bytes());
    dst[12] = (6u8) << 4;
    dst[13] = SYN | ACK;
    dst[14..16].copy_from_slice(&WINDOW.to_be_bytes());
    dst[16] = 0;
    dst[17] = 0;
    dst[18] = 0;
    dst[19] = 0;
    dst[20] = OPT_MSS;
    dst[21] = OPT_MSS_LEN;
    dst[22..24].copy_from_slice(&OUR_MSS.to_be_bytes());
    let c = checksum_tx(src_ip, dst_ip, &dst[..SYNACK_HLEN]);
    dst[16..18].copy_from_slice(&c.to_be_bytes());
    SYNACK_HLEN
}

/// Header + payload. Data offset 5. Used for ACK/data/FIN/RST.
pub fn write_seg(
    dst: &mut [u8],
    src_port: u16,
    dst_port: u16,
    seq: u32,
    ack: u32,
    flags: u8,
    payload: &[u8],
    src_ip: &[u8; 4],
    dst_ip: &[u8; 4],
) -> usize {
    let n = HDR_MIN + payload.len();
    if dst.len() < n {
        panic!("tcp: seg buf {} < {n}", dst.len());
    }
    dst[0..2].copy_from_slice(&src_port.to_be_bytes());
    dst[2..4].copy_from_slice(&dst_port.to_be_bytes());
    dst[4..8].copy_from_slice(&seq.to_be_bytes());
    dst[8..12].copy_from_slice(&ack.to_be_bytes());
    dst[12] = (5u8) << 4;
    dst[13] = flags;
    dst[14..16].copy_from_slice(&WINDOW.to_be_bytes());
    dst[16] = 0;
    dst[17] = 0;
    dst[18] = 0;
    dst[19] = 0;
    if !payload.is_empty() {
        dst[HDR_MIN..n].copy_from_slice(payload);
    }
    let c = checksum_tx(src_ip, dst_ip, &dst[..n]);
    dst[16..18].copy_from_slice(&c.to_be_bytes());
    n
}

fn same_tuple(t: &Tcb, src: &[u8; 4], sport: u16) -> bool {
    t.remote_ip == *src && t.remote_port == sport
}

fn synack(t: &Tcb) -> Out {
    Out::SynAck {
        dst_ip: t.remote_ip,
        dst_port: t.remote_port,
        seq: t.snd_isn,
        ack: t.rcv_nxt,
    }
}

fn seg(t: &Tcb, seq: u32, flags: u8, data_len: usize) -> Out {
    Out::Seg {
        dst_ip: t.remote_ip,
        dst_port: t.remote_port,
        seq,
        ack: t.rcv_nxt,
        flags,
        data_len,
    }
}

fn ack_only(t: &Tcb) -> Out {
    seg(t, t.snd_nxt, ACK, 0)
}

fn rst_seg(t: &Tcb) -> Out {
    seg(t, t.snd_nxt, RST | ACK, 0)
}

fn listen_reset(t: &mut Tcb) {
    // EOF is sticky until `take_eof`. Truncated TIME_WAIT must not
    // swallow the `recv` 0 the app waits on (D-0053).
    let eof = t.eof;
    t.state = State::Listen;
    t.remote_ip = [0; 4];
    t.remote_port = 0;
    t.rx_len = 0;
    t.rx_pending = false;
    t.unacked_len = 0;
    t.unacked_fin = false;
    t.inflight = false;
    t.deadline = 0;
    t.tries = 0;
    t.saw_data = false;
    t.app_sent = false;
    t.eof = eof;
    t.peer_fin = false;
}

fn time_wait(t: &mut Tcb) {
    println!("TCP TIME_WAIT (truncated) → LISTEN");
    listen_reset(t);
}

fn hold_acks() -> bool {
    cfg!(feature = "tcp-drop-first-tx") && unsafe { TCB.rexmit } == 0 && unsafe { TCB.app_sent }
}

fn on_their_ack(t: &mut Tcb, ack: u32) {
    if !t.inflight {
        return;
    }
    if ack != t.snd_nxt {
        return;
    }
    if hold_acks() {
        return;
    }
    t.snd_una = ack;
    t.inflight = false;
    t.deadline = 0;
    t.unacked_len = 0;
    t.unacked_fin = false;
    match t.state {
        State::FinWait1 => {
            t.state = State::FinWait2;
            println!("tcp: FIN_WAIT_2");
            if t.peer_fin {
                time_wait(t);
            }
        }
        State::LastAck => {
            println!("tcp: LAST_ACK complete");
            listen_reset(t);
        }
        _ => {}
    }
}

fn queue_rx(t: &mut Tcb, data: &[u8]) -> bool {
    if data.is_empty() {
        return true;
    }
    // One segment: a second payload before recv consumes the first is
    // dropped, not concatenated (D-0053).
    if t.rx_pending {
        unsafe { DROP_UNEXPECTED = DROP_UNEXPECTED.wrapping_add(1) };
        println!("tcp: drop extra data (one-segment request)");
        return false;
    }
    if data.len() > PAYLOAD_MAX {
        unsafe { DROP_UNEXPECTED = DROP_UNEXPECTED.wrapping_add(1) };
        println!("tcp: drop data len={}", data.len());
        return false;
    }
    t.rx[..data.len()].copy_from_slice(data);
    t.rx_len = data.len();
    t.rx_pending = true;
    t.saw_data = true;
    true
}

fn rx_fin(t: &mut Tcb) {
    let old = t.rcv_nxt;
    t.rcv_nxt = t.rcv_nxt.wrapping_add(1);
    println!(
        "tcp: RX FIN seq={} rcv_nxt {} -> {} (FIN consumes 1)",
        old, old, t.rcv_nxt
    );
    if t.saw_data || t.app_sent {
        t.eof = true;
    }
}

fn arm_rto(t: &mut Tcb) {
    t.inflight = true;
    t.tries = 1;
    t.deadline = csr::time::read().wrapping_add(RTO_TICKS);
}

fn send_unacked(t: &mut Tcb) -> Out {
    let seq = t.snd_una;
    let mut flags = ACK;
    if t.unacked_len != 0 {
        flags |= PSH;
    }
    if t.unacked_fin {
        flags |= FIN;
        let fin_seq = seq.wrapping_add(t.unacked_len as u32);
        let new_nxt = fin_seq.wrapping_add(1);
        println!(
            "tcp: TX FIN seq={} snd_nxt {} -> {} ack={} (FIN consumes 1)",
            fin_seq, t.snd_nxt, new_nxt, t.rcv_nxt
        );
        t.snd_nxt = new_nxt;
    } else {
        t.snd_nxt = seq.wrapping_add(t.unacked_len as u32);
    }
    arm_rto(t);
    seg(t, seq, flags, t.unacked_len)
}

/// App `send`. One outstanding segment. FIN bit is [`crate::syscall::SEND_FIN`].
pub fn app_send(data: &[u8], fin: bool) -> Out {
    let t = tcb();
    if t.state != State::Established && t.state != State::CloseWait {
        return Out::None;
    }
    if t.app_sent || t.inflight {
        return Out::None;
    }
    if data.len() > PAYLOAD_MAX {
        return Out::None;
    }
    t.unacked[..data.len()].copy_from_slice(data);
    t.unacked_len = data.len();
    t.unacked_fin = fin;
    t.app_sent = true;
    t.snd_una = t.snd_nxt;
    if t.state == State::CloseWait && fin {
        t.state = State::LastAck;
        println!("tcp: LAST_ACK");
    } else if fin {
        t.state = State::FinWait1;
        println!("tcp: FIN_WAIT_1");
    }
    send_unacked(t)
}

pub fn pending() -> Option<&'static [u8]> {
    let t = unsafe { &*core::ptr::addr_of!(TCB) };
    if t.rx_pending {
        Some(&t.rx[..t.rx_len])
    } else {
        None
    }
}

pub fn consume() {
    let t = tcb();
    t.rx_pending = false;
    t.rx_len = 0;
}

pub fn take_eof() -> bool {
    let t = tcb();
    // Hold EOF while our segment is inflight so the `recv` poll loop
    // keeps running and can fire the RTO (`tcp-drop-first-tx`).
    if t.eof && !t.rx_pending && !t.inflight {
        t.eof = false;
        true
    } else {
        false
    }
}

pub fn check_rto() -> Out {
    let t = tcb();
    if !t.inflight {
        return Out::None;
    }
    let now = csr::time::read();
    // Future deadline: wrapping sub is huge. Due: sub is small.
    if now.wrapping_sub(t.deadline) > usize::MAX / 2 {
        return Out::None;
    }
    if t.tries >= RTO_MAX {
        println!("tcp: RTO RST after {} attempts", t.tries);
        let out = rst_seg(t);
        listen_reset(t);
        return out;
    }
    t.tries = t.tries.saturating_add(1);
    t.rexmit = t.rexmit.wrapping_add(1);
    t.deadline = now.wrapping_add(RTO_TICKS);
    let seq = t.snd_una;
    let mut flags = ACK;
    if t.unacked_len != 0 {
        flags |= PSH;
    }
    if t.unacked_fin {
        flags |= FIN;
    }
    println!(
        "TCP RETRANSMIT seq={} try={} len={}",
        seq, t.tries, t.unacked_len
    );
    seg(t, seq, flags, t.unacked_len)
}

/// Parse and drive the TCB. Remote bytes never panic.
pub fn handle(payload: &[u8], src: &[u8; 4], dst: &[u8; 4]) -> Out {
    if payload.len() < HDR_MIN {
        unsafe { DROP_SHORT = DROP_SHORT.wrapping_add(1) };
        println!("tcp: drop short len={}", payload.len());
        return Out::None;
    }
    let doff = payload[12] >> 4;
    let hlen = (doff as usize) * 4;
    if doff < 5 || hlen < HDR_MIN || payload.len() < hlen {
        unsafe { DROP_DOFF = DROP_DOFF.wrapping_add(1) };
        println!(
            "tcp: drop doff={doff} hlen={hlen} seg.len={}",
            payload.len()
        );
        return Out::None;
    }
    if !checksum_valid(src, dst, payload) {
        unsafe { DROP_CSUM = DROP_CSUM.wrapping_add(1) };
        println!("tcp: drop bad checksum");
        return Out::None;
    }
    let sport = be16(payload, 0);
    let dport = be16(payload, 2);
    let seq = be32(payload, 4);
    let ack = be32(payload, 8);
    let flags = payload[13];
    let data = &payload[hlen..];

    if dport != LISTEN_PORT {
        unsafe { DROP_PORT = DROP_PORT.wrapping_add(1) };
        println!("tcp: drop dport={dport}");
        return Out::None;
    }

    let t = tcb();

    if flags & RST != 0 {
        if t.state != State::Listen && same_tuple(t, src, sport) {
            println!("tcp: RX RST; LISTEN");
            listen_reset(t);
        }
        return Out::None;
    }

    if flags & SYN != 0 && flags & ACK == 0 {
        let Some(mss) = parse_mss(payload, hlen) else {
            unsafe { DROP_OPT = DROP_OPT.wrapping_add(1) };
            println!("tcp: drop bad option");
            return Out::None;
        };
        match t.state {
            State::Listen => {
                let isn = csr::time::read() as u32;
                t.remote_ip = *src;
                t.remote_port = sport;
                t.their_isn = seq;
                t.rcv_nxt = seq.wrapping_add(1);
                t.snd_isn = isn;
                t.snd_una = isn.wrapping_add(1);
                t.snd_nxt = isn.wrapping_add(1);
                t.mss = mss;
                t.state = State::SynRcvd;
                println!("tcp: SYN_RCVD sport={sport} their_isn={seq} mss={mss} our_isn={isn}");
                return synack(t);
            }
            State::SynRcvd if same_tuple(t, src, sport) && seq == t.their_isn => {
                println!("tcp: duplicate SYN in SYN_RCVD; re-send SYN/ACK");
                return synack(t);
            }
            _ => {
                unsafe { DROP_BUSY = DROP_BUSY.wrapping_add(1) };
                println!("tcp: drop busy SYN");
                return Out::None;
            }
        }
    }

    if t.state == State::Listen {
        // Drop, do not RST: the happy-path pcap forbids RST, and a
        // retransmitted peer FIN after truncated TIME_WAIT lands here
        // (D-0053). Silence is the one-shot-server reading of D-0041.
        unsafe { DROP_UNEXPECTED = DROP_UNEXPECTED.wrapping_add(1) };
        println!("tcp: drop unexpected flags={flags:#04x} in LISTEN");
        return Out::None;
    }

    if !same_tuple(t, src, sport) {
        unsafe { DROP_BUSY = DROP_BUSY.wrapping_add(1) };
        println!("tcp: drop busy 4-tuple");
        return Out::None;
    }

    if flags & ACK != 0 {
        on_their_ack(t, ack);
        if t.state == State::Listen {
            return Out::None;
        }
    }

    if t.state == State::SynRcvd {
        if flags & ACK != 0 && flags & SYN == 0 && ack == t.snd_nxt && seq == t.rcv_nxt {
            t.state = State::Established;
            unsafe { ESTABLISHED = ESTABLISHED.wrapping_add(1) };
            println!("TCP ESTABLISHED");
        } else {
            unsafe { DROP_UNEXPECTED = DROP_UNEXPECTED.wrapping_add(1) };
            println!("tcp: drop unexpected in SYN_RCVD flags={flags:#04x}");
            return Out::None;
        }
    }

    let mut need_ack = false;
    if !data.is_empty() {
        if seq != t.rcv_nxt {
            println!("tcp: drop out-of-order seq={seq} rcv_nxt={}", t.rcv_nxt);
            return ack_only(t);
        }
        if queue_rx(t, data) {
            t.rcv_nxt = t.rcv_nxt.wrapping_add(data.len() as u32);
            need_ack = true;
        }
    }

    if flags & FIN != 0 {
        let fin_seq = seq.wrapping_add(data.len() as u32);
        if fin_seq != t.rcv_nxt {
            println!(
                "tcp: drop out-of-order FIN seq={fin_seq} rcv_nxt={}",
                t.rcv_nxt
            );
            return ack_only(t);
        }
        rx_fin(t);
        t.peer_fin = true;
        need_ack = true;
        match t.state {
            State::Established => {
                t.state = State::CloseWait;
                println!("tcp: CLOSE_WAIT");
                if !t.saw_data && !t.app_sent {
                    t.unacked_len = 0;
                    t.unacked_fin = true;
                    t.snd_una = t.snd_nxt;
                    t.state = State::LastAck;
                    println!("tcp: LAST_ACK (unused TCB)");
                    return send_unacked(t);
                }
            }
            State::FinWait1 => {
                // Our FIN may still be inflight (drop-first-tx holds the
                // ACK). Stay here until on_their_ack; do not TIME_WAIT yet.
            }
            State::FinWait2 => {
                let ack = t.rcv_nxt;
                let seqn = t.snd_nxt;
                time_wait(t);
                return ack_only_saved(src, sport, ack, seqn);
            }
            State::CloseWait | State::LastAck => {}
            _ => {}
        }
    }

    if need_ack {
        ack_only(t)
    } else {
        Out::None
    }
}

fn ack_only_saved(src: &[u8; 4], sport: u16, ack: u32, seq: u32) -> Out {
    Out::Seg {
        dst_ip: *src,
        dst_port: sport,
        seq,
        ack,
        flags: ACK,
        data_len: 0,
    }
}

pub fn selftest() {
    let ours = [10, 0, 2, 15];
    let peer = [10, 0, 2, 2];

    let mut syn = [0u8; 28];
    syn[0..2].copy_from_slice(&12345u16.to_be_bytes());
    syn[2..4].copy_from_slice(&LISTEN_PORT.to_be_bytes());
    syn[4..8].copy_from_slice(&0x1000u32.to_be_bytes());
    syn[12] = 7 << 4;
    syn[13] = SYN;
    syn[14..16].copy_from_slice(&WINDOW.to_be_bytes());
    syn[20] = OPT_MSS;
    syn[21] = OPT_MSS_LEN;
    syn[22..24].copy_from_slice(&1460u16.to_be_bytes());
    syn[24] = OPT_NOP;
    syn[25] = 3;
    syn[26] = 3;
    syn[27] = 8;
    if parse_mss(&syn, 28) != Some(1460) {
        panic!("tcp: MSS not parsed / window-scale not skipped");
    }
    let c = checksum_tx(&peer, &ours, &syn);
    syn[16..18].copy_from_slice(&c.to_be_bytes());
    if !checksum_valid(&peer, &ours, &syn) {
        panic!("tcp: SYN with options failed checksum");
    }

    let mut syn20 = [0u8; 20];
    syn20[0..2].copy_from_slice(&1u16.to_be_bytes());
    syn20[2..4].copy_from_slice(&LISTEN_PORT.to_be_bytes());
    syn20[12] = 5 << 4;
    syn20[13] = SYN;
    if parse_mss(&syn20, 20) != Some(DEFAULT_MSS) {
        panic!("tcp: missing MSS must default to 536");
    }

    let mut bad = [0u8; 21];
    bad[12] = 6 << 4;
    bad[20] = OPT_MSS;
    if parse_mss(&bad, 21).is_some() {
        panic!("tcp: truncated option must not parse");
    }

    let mut synack = [0u8; SYNACK_HLEN];
    let n = write_synack(
        &mut synack,
        LISTEN_PORT,
        12345,
        0x2000,
        0x1001,
        &ours,
        &peer,
    );
    if n != SYNACK_HLEN || synack[12] >> 4 != 6 || synack[13] != (SYN | ACK) {
        panic!("tcp: SYN/ACK build");
    }
    if be32(&synack, 8) != 0x1001 || !checksum_valid(&ours, &peer, &synack[..n]) {
        panic!("tcp: SYN/ACK checksum / ack");
    }

    // FIN consumes one: data_len 4 at seq 0x2001 → FIN seq 0x2005, snd_nxt 0x2006.
    let payload = b"http";
    let seq = 0x2001u32;
    let fin_seq = seq.wrapping_add(payload.len() as u32);
    let snd_nxt = fin_seq.wrapping_add(1);
    if fin_seq != 0x2005 || snd_nxt != 0x2006 {
        panic!("tcp: FIN sequence arithmetic");
    }
    let mut finseg = [0u8; 40];
    let m = write_seg(
        &mut finseg,
        LISTEN_PORT,
        12345,
        seq,
        0x1001,
        ACK | PSH | FIN,
        payload,
        &ours,
        &peer,
    );
    if m != HDR_MIN + payload.len() || !checksum_valid(&ours, &peer, &finseg[..m]) {
        panic!("tcp: data+FIN checksum");
    }
    if finseg[13] & FIN == 0 {
        panic!("tcp: FIN flag");
    }

    println!("TCP SYN/ACK BUILD OK");
    println!("TCP LISTEN");
}
