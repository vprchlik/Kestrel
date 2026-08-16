//! TCP passive open (RFC 793, D-0041, D-0052).
//!
//! Owns one listener and one TCB: LISTEN → SYN_RCVD → ESTABLISHED.
//! Every segment's data offset is read from the high nibble of byte 12
//! (`doff * 4`); nothing assumes a 20-byte header. MSS (kind 2) is
//! parsed from the SYN; every other option is skipped by its length
//! byte. Unrecognized options are not RST. Duplicate SYN in SYN_RCVD
//! re-sends the same SYN/ACK (same ISN) so the handshake self-heals
//! when the peer retransmits. ISN is `rdtime` low bits. Checksums use
//! the IPv4 pseudo-header both directions; a wrong TX checksum is a
//! silently discarded segment. T3.10 does not send RST, transfer data,
//! or close: unexpected segments increment a counter and are dropped.
//! Without this module hostfwd SYNs stay `drop_proto` and T3.10 cannot
//! pass.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::checksum;
use crate::csr;
use crate::ipv4;
use crate::println;

/// Minimum TCP header. RFC 793. Data offset 5 = 20 bytes.
const HDR_MIN: usize = 20;
/// Listen port. PLAN T3.10 / `hostfwd=tcp::8080-:80`.
pub const LISTEN_PORT: u16 = 80;
/// Advertised window. D-0041.
const WINDOW: u16 = 8192;
/// RFC default when the SYN omitted MSS.
const DEFAULT_MSS: u16 = 536;
/// MSS we advertise (1500 − 20 IPv4 − 20 TCP).
const OUR_MSS: u16 = 1460;
/// SYN/ACK header with an MSS option: data offset 6.
const SYNACK_HLEN: usize = 24;

const FIN: u8 = 0x01;
const SYN: u8 = 0x02;
const RST: u8 = 0x04;
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
}

struct Tcb {
    state: State,
    remote_ip: [u8; 4],
    remote_port: u16,
    their_isn: u32,
    rcv_nxt: u32,
    snd_isn: u32,
    snd_nxt: u32,
    mss: u16,
}

static mut TCB: Tcb = Tcb {
    state: State::Listen,
    remote_ip: [0; 4],
    remote_port: 0,
    their_isn: 0,
    rcv_nxt: 0,
    snd_isn: 0,
    snd_nxt: 0,
    mss: DEFAULT_MSS,
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

pub fn note_noarp() {
    unsafe { DROP_NOARP = DROP_NOARP.wrapping_add(1) };
    println!("tcp: drop no arp cache for gateway");
}

/// SYN/ACK the NIC should transmit. `seq` is our ISN; `ack` is their ISN+1.
pub struct SynAck {
    pub dst_ip: [u8; 4],
    pub dst_port: u16,
    pub seq: u32,
    pub ack: u32,
}

fn be16(b: &[u8], off: usize) -> u16 {
    u16::from_be_bytes([b[off], b[off + 1]])
}

fn be32(b: &[u8], off: usize) -> u32 {
    u32::from_be_bytes([b[off], b[off + 1], b[off + 2], b[off + 3]])
}

/// RFC 793 pseudo-header: src, dst, zero, proto 6, TCP length.
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

/// Value to store in the TCP checksum field. Unlike UDP, 0 is left as 0.
pub fn checksum_tx(src: &[u8; 4], dst: &[u8; 4], tcp: &[u8]) -> u16 {
    !tcp_sum(src, dst, tcp)
}

fn checksum_valid(src: &[u8; 4], dst: &[u8; 4], tcp: &[u8]) -> bool {
    tcp_sum(src, dst, tcp) == 0xffff
}

/// Walk options starting at byte 20, stopping at `hlen`. Kind 0 ends,
/// kind 1 skips one byte, anything else uses the length byte. Unknown
/// kinds are skipped, not rejected. Invalid length → `None`.
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

/// Write a SYN/ACK with an MSS option (data offset 6). Returns length.
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
    // Data offset 6 (24 bytes). Flags SYN|ACK. Nothing here is 20.
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

fn same_tuple(tcb: &Tcb, src: &[u8; 4], sport: u16) -> bool {
    tcb.remote_ip == *src && tcb.remote_port == sport
}

/// Parse and drive the TCB. `None` = no TX. Remote bytes never panic.
pub fn handle(payload: &[u8], src: &[u8; 4], dst: &[u8; 4]) -> Option<SynAck> {
    if payload.len() < HDR_MIN {
        unsafe { DROP_SHORT = DROP_SHORT.wrapping_add(1) };
        println!("tcp: drop short len={}", payload.len());
        return None;
    }
    // Data offset is the high nibble of byte 12, in 32-bit words.
    // A peer with options has doff > 5; assuming 20 bytes here would
    // treat those options as payload (or checksum the wrong length).
    let doff = payload[12] >> 4;
    let hlen = (doff as usize) * 4;
    if doff < 5 || hlen < HDR_MIN || payload.len() < hlen {
        unsafe { DROP_DOFF = DROP_DOFF.wrapping_add(1) };
        println!(
            "tcp: drop doff={doff} hlen={hlen} seg.len={}",
            payload.len()
        );
        return None;
    }
    if !checksum_valid(src, dst, payload) {
        unsafe { DROP_CSUM = DROP_CSUM.wrapping_add(1) };
        println!("tcp: drop bad checksum");
        return None;
    }
    let sport = be16(payload, 0);
    let dport = be16(payload, 2);
    let seq = be32(payload, 4);
    let ack = be32(payload, 8);
    let flags = payload[13];
    let data_len = payload.len() - hlen;

    if dport != LISTEN_PORT {
        unsafe { DROP_PORT = DROP_PORT.wrapping_add(1) };
        println!("tcp: drop dport={dport}");
        return None;
    }
    if flags & RST != 0 {
        unsafe { DROP_UNEXPECTED = DROP_UNEXPECTED.wrapping_add(1) };
        println!("tcp: drop rst");
        return None;
    }

    let tcb = unsafe { &mut *core::ptr::addr_of_mut!(TCB) };

    if flags & SYN != 0 && flags & ACK == 0 {
        let Some(mss) = parse_mss(payload, hlen) else {
            unsafe { DROP_OPT = DROP_OPT.wrapping_add(1) };
            println!("tcp: drop bad option");
            return None;
        };
        match tcb.state {
            State::Listen => {
                // SYN consumes a sequence number: ACK = their_isn+1,
                // and SND.NXT advances past our ISN.
                let isn = csr::time::read() as u32;
                tcb.remote_ip = *src;
                tcb.remote_port = sport;
                tcb.their_isn = seq;
                tcb.rcv_nxt = seq.wrapping_add(1);
                tcb.snd_isn = isn;
                tcb.snd_nxt = isn.wrapping_add(1);
                tcb.mss = mss;
                tcb.state = State::SynRcvd;
                println!("tcp: SYN_RCVD sport={sport} their_isn={seq} mss={mss} our_isn={isn}");
                return Some(SynAck {
                    dst_ip: *src,
                    dst_port: sport,
                    seq: tcb.snd_isn,
                    ack: tcb.rcv_nxt,
                });
            }
            State::SynRcvd if same_tuple(tcb, src, sport) && seq == tcb.their_isn => {
                println!("tcp: duplicate SYN in SYN_RCVD; re-send SYN/ACK");
                return Some(SynAck {
                    dst_ip: tcb.remote_ip,
                    dst_port: tcb.remote_port,
                    seq: tcb.snd_isn,
                    ack: tcb.rcv_nxt,
                });
            }
            _ => {
                unsafe { DROP_BUSY = DROP_BUSY.wrapping_add(1) };
                println!("tcp: drop busy SYN");
                return None;
            }
        }
    }

    match tcb.state {
        State::Listen => {
            unsafe { DROP_UNEXPECTED = DROP_UNEXPECTED.wrapping_add(1) };
            println!("tcp: drop unexpected flags={flags:#04x} in LISTEN");
            None
        }
        State::SynRcvd if same_tuple(tcb, src, sport) => {
            if flags & ACK != 0 && flags & SYN == 0 && ack == tcb.snd_nxt && seq == tcb.rcv_nxt {
                tcb.state = State::Established;
                unsafe { ESTABLISHED = ESTABLISHED.wrapping_add(1) };
                println!("TCP ESTABLISHED");
                if data_len != 0 || flags & FIN != 0 {
                    unsafe { DROP_UNEXPECTED = DROP_UNEXPECTED.wrapping_add(1) };
                    println!(
                        "tcp: drop unexpected on handshake ACK data={data_len} flags={flags:#04x}"
                    );
                }
                None
            } else {
                unsafe { DROP_UNEXPECTED = DROP_UNEXPECTED.wrapping_add(1) };
                println!("tcp: drop unexpected in SYN_RCVD flags={flags:#04x} seq={seq} ack={ack}");
                None
            }
        }
        State::Established if same_tuple(tcb, src, sport) => {
            unsafe { DROP_UNEXPECTED = DROP_UNEXPECTED.wrapping_add(1) };
            println!("tcp: drop unexpected in ESTABLISHED flags={flags:#04x}");
            None
        }
        _ => {
            unsafe { DROP_BUSY = DROP_BUSY.wrapping_add(1) };
            println!("tcp: drop busy 4-tuple");
            None
        }
    }
}

/// Data-offset walk, MSS skip, SYN consumption, pseudo-header checksum.
pub fn selftest() {
    let ours = [10, 0, 2, 15];
    let peer = [10, 0, 2, 2];

    // SYN with MSS, NOP, window-scale (unrecognized), EOL. doff=7 (28 bytes).
    // Bytes after the 20-byte base are options, not payload.
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
    syn[25] = 3; // window scale, skipped by length
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

    // A 20-byte SYN (doff=5) has no MSS → default 536. Confirms we read
    // doff rather than assuming options always follow.
    let mut syn20 = [0u8; 20];
    syn20[0..2].copy_from_slice(&1u16.to_be_bytes());
    syn20[2..4].copy_from_slice(&LISTEN_PORT.to_be_bytes());
    syn20[12] = 5 << 4;
    syn20[13] = SYN;
    if parse_mss(&syn20, 20) != Some(DEFAULT_MSS) {
        panic!("tcp: missing MSS must default to 536");
    }

    // Truncated option (kind without a length) is a drop, not a skip.
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
    if n != SYNACK_HLEN {
        panic!("tcp: SYN/ACK length");
    }
    if synack[12] >> 4 != 6 {
        panic!("tcp: SYN/ACK data offset must be 6 (MSS option)");
    }
    if synack[13] != (SYN | ACK) {
        panic!("tcp: SYN/ACK flags");
    }
    if be32(&synack, 8) != 0x1001 {
        panic!("tcp: SYN/ACK ack is not their_isn+1");
    }
    if !checksum_valid(&ours, &peer, &synack[..n]) {
        panic!("tcp: SYN/ACK checksum");
    }
    let pseudo = pseudo_header(&ours, &peer, n as u16);
    if pseudo[8] != 0 || pseudo[9] != ipv4::PROTO_TCP {
        panic!("tcp: pseudo-header zero/proto");
    }
    println!("TCP SYN/ACK BUILD OK");
    println!("TCP LISTEN");
}
