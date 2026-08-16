//! ICMP echo request and reply (RFC 792, D-0048).
//!
//! Owns type 8 → 0, the ICMP checksum, and the drop counters for a
//! truncated or corrupt message. The echo-server writer is exercised by
//! a build self-test; under slirp user-net the harness can only test
//! the client (guest ping of 10.0.2.2). Remote bytes never panic.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::checksum;
use crate::println;

/// Echo reply. RFC 792.
pub const TYPE_ECHO_REPLY: u8 = 0;
/// Echo request. RFC 792.
pub const TYPE_ECHO_REQ: u8 = 8;
const HDR: usize = 8;

static mut DROP_SHORT: u32 = 0;
static mut DROP_CSUM: u32 = 0;
static mut DROP_TYPE: u32 = 0;

pub fn drop_short() -> u32 {
    unsafe { DROP_SHORT }
}
pub fn drop_csum() -> u32 {
    unsafe { DROP_CSUM }
}
pub fn drop_type() -> u32 {
    unsafe { DROP_TYPE }
}

fn be16(b: &[u8], off: usize) -> u16 {
    u16::from_be_bytes([b[off], b[off + 1]])
}

pub enum Msg<'a> {
    EchoReq { id: u16, seq: u16, raw: &'a [u8] },
    EchoReply { id: u16, seq: u16 },
}

/// Parse an ICMP message. `None` = dropped.
pub fn parse(payload: &[u8]) -> Option<Msg<'_>> {
    if payload.len() < HDR {
        unsafe { DROP_SHORT = DROP_SHORT.wrapping_add(1) };
        println!("icmp: drop short len={}", payload.len());
        return None;
    }
    if !checksum::valid(payload) {
        unsafe { DROP_CSUM = DROP_CSUM.wrapping_add(1) };
        println!("icmp: drop bad checksum");
        return None;
    }
    let typ = payload[0];
    let code = payload[1];
    if code != 0 {
        unsafe { DROP_TYPE = DROP_TYPE.wrapping_add(1) };
        println!("icmp: drop type={typ} code={code}");
        return None;
    }
    let id = be16(payload, 4);
    let seq = be16(payload, 6);
    match typ {
        TYPE_ECHO_REQ => Some(Msg::EchoReq {
            id,
            seq,
            raw: payload,
        }),
        TYPE_ECHO_REPLY => Some(Msg::EchoReply { id, seq }),
        _ => {
            unsafe { DROP_TYPE = DROP_TYPE.wrapping_add(1) };
            println!("icmp: drop type={typ}");
            None
        }
    }
}

/// Fill an echo request; returns the ICMP length (8 + data).
pub fn write_echo_req(icmp: &mut [u8], id: u16, seq: u16, data: &[u8]) -> usize {
    let n = HDR + data.len();
    if icmp.len() < n {
        panic!("icmp: echo req buf {} < {n}", icmp.len());
    }
    icmp[0] = TYPE_ECHO_REQ;
    icmp[1] = 0;
    icmp[2] = 0;
    icmp[3] = 0;
    icmp[4..6].copy_from_slice(&id.to_be_bytes());
    icmp[6..8].copy_from_slice(&seq.to_be_bytes());
    icmp[HDR..n].copy_from_slice(data);
    let c = checksum::checksum(&icmp[..n]);
    icmp[2..4].copy_from_slice(&c.to_be_bytes());
    n
}

/// Type 8 → 0, same identifier/sequence/data, new checksum. Returns length.
pub fn write_echo_reply(dst: &mut [u8], req: &[u8]) -> usize {
    if req.len() < HDR {
        panic!("icmp: echo reply copy {} < {HDR}", req.len());
    }
    if dst.len() < req.len() {
        panic!("icmp: echo reply buf {} < {}", dst.len(), req.len());
    }
    dst[..req.len()].copy_from_slice(req);
    dst[0] = TYPE_ECHO_REPLY;
    dst[2] = 0;
    dst[3] = 0;
    let c = checksum::checksum(&dst[..req.len()]);
    dst[2..4].copy_from_slice(&c.to_be_bytes());
    req.len()
}

/// Prove the server writer without a wire (D-0048).
pub fn reply_selftest() {
    let data = b"whimbrel";
    let mut req = [0u8; 16];
    let n = write_echo_req(&mut req, 1, 1, data);
    if n != 16 || !checksum::valid(&req[..n]) || req[0] != TYPE_ECHO_REQ {
        panic!("icmp: echo request selftest");
    }
    let mut rep = [0u8; 16];
    let m = write_echo_reply(&mut rep, &req[..n]);
    if m != n
        || rep[0] != TYPE_ECHO_REPLY
        || !checksum::valid(&rep[..m])
        || &rep[8..16] != data
        || be16(&rep, 4) != 1
        || be16(&rep, 6) != 1
    {
        panic!("icmp: echo reply build selftest");
    }
    println!("ICMP REPLY BUILD OK");
}
