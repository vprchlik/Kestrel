//! UDP parse, echo, and pseudo-header checksum (RFC 768, D-0050).
//!
//! Owns the 12-byte IPv4 pseudo-header (source IP, dest IP, zero byte,
//! protocol 17, UDP length) plus the real header and payload. UDP
//! length is summed twice — once in the pseudo-header, once as the
//! Length field in the datagram — which is RFC 768, not a double-count
//! bug. A computed 0 is stored as 0xFFFF (RFC 768: 0 means "no
//! checksum"). A received 0 is dropped — stricter than the RFC, which
//! permits zero as optional-checksum; that is a deliberate deviation
//! (D-0050), not an accident of the parser. Echo mirrors payload and
//! length, swaps ports, and recomputes checksums. Without this module
//! hostfwd UDP has nowhere to land and T3.8's `nc -u` test cannot pass.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::checksum;
use crate::ipv4;
use crate::println;

/// UDP header length. RFC 768.
const HDR: usize = 8;
/// Echo port. PLAN T3.8 / `hostfwd=udp::7777-:7`.
pub const ECHO_PORT: u16 = 7;

pub static mut DROP_SHORT: u32 = 0;
pub static mut DROP_LEN: u32 = 0;
pub static mut DROP_CSUM: u32 = 0;
pub static mut DROP_PORT: u32 = 0;

fn be16(b: &[u8], off: usize) -> u16 {
    u16::from_be_bytes([b[off], b[off + 1]])
}

/// RFC 768 pseudo-header: src, dst, zero, proto, UDP length.
fn pseudo_header(src: &[u8; 4], dst: &[u8; 4], udp_len: u16) -> [u8; 12] {
    let mut p = [0u8; 12];
    p[0..4].copy_from_slice(src);
    p[4..8].copy_from_slice(dst);
    p[8] = 0;
    p[9] = ipv4::PROTO_UDP;
    p[10..12].copy_from_slice(&udp_len.to_be_bytes());
    p
}

/// One's-complement sum of pseudo-header + UDP datagram (header field
/// included as stored). Folded until stable.
fn udp_sum(src: &[u8; 4], dst: &[u8; 4], udp: &[u8]) -> u16 {
    let udp_len = udp.len() as u16;
    let pseudo = pseudo_header(src, dst, udp_len);
    checksum::fold(checksum::accumulate(checksum::accumulate(0, &pseudo), udp))
}

/// Value to store in the UDP checksum field. `0` becomes `0xFFFF`.
pub fn store_checksum(c: u16) -> u16 {
    if c == 0 {
        0xffff
    } else {
        c
    }
}

/// Value to store in the UDP checksum field. `0` becomes `0xFFFF`.
pub fn checksum_tx(src: &[u8; 4], dst: &[u8; 4], udp: &[u8]) -> u16 {
    store_checksum(!udp_sum(src, dst, udp))
}

fn checksum_valid(src: &[u8; 4], dst: &[u8; 4], udp: &[u8]) -> bool {
    udp_sum(src, dst, udp) == 0xffff
}

pub struct Echo<'a> {
    pub src_port: u16,
    pub raw: &'a [u8],
}

/// Parse a UDP datagram destined for us. `None` = dropped.
/// `src`/`dst` are the IPv4 addresses (for the pseudo-header).
pub fn parse<'a>(payload: &'a [u8], src: &[u8; 4], dst: &[u8; 4]) -> Option<Echo<'a>> {
    if payload.len() < HDR {
        unsafe { DROP_SHORT = DROP_SHORT.wrapping_add(1) };
        println!("udp: drop short len={}", payload.len());
        return None;
    }
    let ulen = be16(payload, 4) as usize;
    if ulen != payload.len() {
        unsafe { DROP_LEN = DROP_LEN.wrapping_add(1) };
        println!("udp: drop length={ulen} payload.len={}", payload.len());
        return None;
    }
    let csum = be16(payload, 6);
    if csum == 0 {
        unsafe { DROP_CSUM = DROP_CSUM.wrapping_add(1) };
        println!("udp: drop checksum=0 (RFC 768 optional-checksum; D-0050 deviation)");
        return None;
    }
    if !checksum_valid(src, dst, payload) {
        unsafe { DROP_CSUM = DROP_CSUM.wrapping_add(1) };
        println!("udp: drop bad checksum");
        return None;
    }
    let dport = be16(payload, 2);
    if dport != ECHO_PORT {
        unsafe { DROP_PORT = DROP_PORT.wrapping_add(1) };
        println!("udp: drop dport={dport}");
        return None;
    }
    Some(Echo {
        src_port: be16(payload, 0),
        raw: payload,
    })
}

impl<'a> Echo<'a> {
    pub fn payload(&self) -> &'a [u8] {
        &self.raw[HDR..]
    }
}

/// Build a datagram: source port, dest port, payload, checksum.
pub fn write_dgram(
    dst_udp: &mut [u8],
    src_port: u16,
    dst_port: u16,
    payload: &[u8],
    src_ip: &[u8; 4],
    dst_ip: &[u8; 4],
) -> usize {
    let n = HDR + payload.len();
    if dst_udp.len() < n {
        panic!("udp: dgram buf {} < {n}", dst_udp.len());
    }
    dst_udp[0..2].copy_from_slice(&src_port.to_be_bytes());
    dst_udp[2..4].copy_from_slice(&dst_port.to_be_bytes());
    dst_udp[4..6].copy_from_slice(&(n as u16).to_be_bytes());
    dst_udp[6] = 0;
    dst_udp[7] = 0;
    dst_udp[HDR..n].copy_from_slice(payload);
    let c = checksum_tx(src_ip, dst_ip, &dst_udp[..n]);
    dst_udp[6..8].copy_from_slice(&c.to_be_bytes());
    n
}

/// Mirror payload and length; swap ports; recompute checksum. Returns length.
#[cfg(not(feature = "fast-boot"))]
pub fn write_echo(dst_udp: &mut [u8], req: &[u8], src_ip: &[u8; 4], dst_ip: &[u8; 4]) -> usize {
    if req.len() < HDR {
        panic!("udp: echo copy {} < {HDR}", req.len());
    }
    if dst_udp.len() < req.len() {
        panic!("udp: echo buf {} < {}", dst_udp.len(), req.len());
    }
    dst_udp[..req.len()].copy_from_slice(req);
    // Swap source and dest ports; length and payload stay.
    dst_udp[0..2].copy_from_slice(&req[2..4]);
    dst_udp[2..4].copy_from_slice(&req[0..2]);
    dst_udp[6] = 0;
    dst_udp[7] = 0;
    let c = checksum_tx(src_ip, dst_ip, &dst_udp[..req.len()]);
    dst_udp[6..8].copy_from_slice(&c.to_be_bytes());
    req.len()
}

/// Pseudo-header layout, 0→0xFFFF, and echo swap (D-0050).
#[cfg(not(feature = "fast-boot"))]
pub fn selftest() {
    let ours = [10, 0, 2, 15];
    let peer = [10, 0, 2, 2];
    let data = b"whimbrel-udp";
    let n = HDR + data.len();
    let mut req = [0u8; 32];
    req[0..2].copy_from_slice(&12345u16.to_be_bytes());
    req[2..4].copy_from_slice(&ECHO_PORT.to_be_bytes());
    req[4..6].copy_from_slice(&(n as u16).to_be_bytes());
    req[HDR..n].copy_from_slice(data);
    // Length appears in the pseudo-header and in req[4..6].
    let pseudo = pseudo_header(&peer, &ours, n as u16);
    if pseudo[8] != 0 || pseudo[9] != ipv4::PROTO_UDP {
        panic!("udp: pseudo-header zero/proto");
    }
    if &pseudo[10..12] != &req[4..6] {
        panic!("udp: UDP length not in both pseudo-header and real header");
    }
    let c = checksum_tx(&peer, &ours, &req[..n]);
    if c == 0 {
        panic!("udp: TX checksum 0 was not rewritten to 0xffff");
    }
    req[6..8].copy_from_slice(&c.to_be_bytes());
    if parse(&req[..n], &peer, &ours).is_none() {
        panic!("udp: selftest request did not parse");
    }
    if store_checksum(0) != 0xffff {
        panic!("udp: computed 0 must be transmitted as 0xffff");
    }

    let mut rep = [0u8; 32];
    let m = write_echo(&mut rep, &req[..n], &ours, &peer);
    if m != n
        || be16(&rep, 0) != ECHO_PORT
        || be16(&rep, 2) != 12345
        || be16(&rep, 4) != n as u16
        || &rep[HDR..n] != data
        || be16(&rep, 6) == 0
        || !checksum_valid(&ours, &peer, &rep[..n])
    {
        panic!("udp: echo swap selftest");
    }
    println!("UDP ECHO BUILD OK");
}
