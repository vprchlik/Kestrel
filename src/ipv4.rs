//! IPv4 parse and header build (RFC 791, D-0040).
//!
//! Owns version/IHL, the header checksum, fragment drop, and honoring
//! IHL for the payload start instead of assuming 20 bytes. Remote bytes
//! never panic: each rejected shape increments a counter. ICMP (1) and
//! UDP (17) are delivered; anything else increments `drop_proto`.
//! Protocol 6 (TCP) from hostfwd SYNs is expected until T3.10 (D-0049)
//! — after TCP exists, a non-zero `drop_proto` is a real drop. TX has
//! no routing — callers put the gateway MAC on Ethernet (D-0047).

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::checksum;
use crate::println;

/// Ethernet header length.
const ETH_HDR: usize = 14;
/// Minimum IHL (20-byte header). RFC 791.
const IHL_MIN: u8 = 5;
/// Version 4 in the high nibble.
const VERSION: u8 = 4;
/// ICMP protocol number. RFC 791.
pub const PROTO_ICMP: u8 = 1;
/// UDP protocol number. RFC 768.
pub const PROTO_UDP: u8 = 17;
/// TCP protocol number. RFC 793. Dropped until T3.10 (D-0049).
const PROTO_TCP: u8 = 6;
/// More-Fragments bit in the 16-bit flags+offset word. RFC 791.
const MF: u16 = 0x2000;
/// 13-bit fragment offset mask.
const OFF_MASK: u16 = 0x1fff;
/// DF; we set it on TX, we do not drop it on RX.
const DF: u16 = 0x4000;

static mut DROP_SHORT: u32 = 0;
static mut DROP_VER: u32 = 0;
static mut DROP_IHL: u32 = 0;
static mut DROP_CSUM: u32 = 0;
static mut DROP_FRAG: u32 = 0;
static mut DROP_DST: u32 = 0;
static mut DROP_PROTO: u32 = 0;

pub fn drop_short() -> u32 {
    unsafe { DROP_SHORT }
}
pub fn drop_ver() -> u32 {
    unsafe { DROP_VER }
}
pub fn drop_ihl() -> u32 {
    unsafe { DROP_IHL }
}
pub fn drop_csum() -> u32 {
    unsafe { DROP_CSUM }
}
pub fn drop_frag() -> u32 {
    unsafe { DROP_FRAG }
}
pub fn drop_dst() -> u32 {
    unsafe { DROP_DST }
}
pub fn drop_proto() -> u32 {
    unsafe { DROP_PROTO }
}

fn be16(b: &[u8], off: usize) -> u16 {
    u16::from_be_bytes([b[off], b[off + 1]])
}

/// IPv4 datagram whose payload starts at IHL*4, length from Total Length.
pub struct Datagram<'a> {
    pub src: [u8; 4],
    pub proto: u8,
    pub payload: &'a [u8],
}

/// Parse an Ethernet frame already classified as EtherType IPv4.
/// `None` = dropped (counter already incremented).
pub fn parse<'a>(eth: &'a [u8], our_ip: &[u8; 4]) -> Option<Datagram<'a>> {
    if eth.len() < ETH_HDR + 20 {
        unsafe { DROP_SHORT = DROP_SHORT.wrapping_add(1) };
        println!("ipv4: drop short frame.len={}", eth.len());
        return None;
    }
    let ip = &eth[ETH_HDR..];
    let ver = ip[0] >> 4;
    if ver != VERSION {
        unsafe { DROP_VER = DROP_VER.wrapping_add(1) };
        println!("ipv4: drop version={ver}");
        return None;
    }
    let ihl = ip[0] & 0x0f;
    if ihl < IHL_MIN {
        unsafe { DROP_IHL = DROP_IHL.wrapping_add(1) };
        println!("ipv4: drop ihl={ihl}");
        return None;
    }
    let hlen = (ihl as usize) * 4;
    if ip.len() < hlen {
        unsafe { DROP_SHORT = DROP_SHORT.wrapping_add(1) };
        println!("ipv4: drop short for ihl={ihl} ip.len={}", ip.len());
        return None;
    }
    let tot = be16(ip, 2) as usize;
    if tot < hlen || ETH_HDR + tot > eth.len() {
        unsafe { DROP_SHORT = DROP_SHORT.wrapping_add(1) };
        println!("ipv4: drop tot_len={tot} hlen={hlen} frame.len={}", eth.len());
        return None;
    }
    if !checksum::valid(&ip[..hlen]) {
        unsafe { DROP_CSUM = DROP_CSUM.wrapping_add(1) };
        println!("ipv4: drop bad header checksum ihl={ihl}");
        return None;
    }
    let frag = be16(ip, 6);
    if frag & MF != 0 || frag & OFF_MASK != 0 {
        unsafe { DROP_FRAG = DROP_FRAG.wrapping_add(1) };
        println!("ipv4: drop fragment flags={frag:#06x}");
        return None;
    }
    let mut src = [0u8; 4];
    let mut dst = [0u8; 4];
    src.copy_from_slice(&ip[12..16]);
    dst.copy_from_slice(&ip[16..20]);
    if &dst != our_ip {
        unsafe { DROP_DST = DROP_DST.wrapping_add(1) };
        println!(
            "ipv4: drop dst {}.{}.{}.{}",
            dst[0], dst[1], dst[2], dst[3]
        );
        return None;
    }
    let proto = ip[9];
    if proto != PROTO_ICMP && proto != PROTO_UDP {
        unsafe { DROP_PROTO = DROP_PROTO.wrapping_add(1) };
        if proto == PROTO_TCP {
            println!("ipv4: drop proto=6 (TCP; expected until T3.10)");
        } else {
            println!("ipv4: drop proto={proto}");
        }
        return None;
    }
    Some(Datagram {
        src,
        proto,
        payload: &ip[hlen..tot],
    })
}

/// Write a 20-byte IPv4 header (IHL=5, DF, TTL 64). Checksum last.
pub fn write_header(ip: &mut [u8], total: u16, proto: u8, src: &[u8; 4], dst: &[u8; 4]) {
    if ip.len() < 20 {
        panic!("ipv4: header buf {} < 20", ip.len());
    }
    ip[0] = (VERSION << 4) | IHL_MIN;
    ip[1] = 0;
    ip[2..4].copy_from_slice(&total.to_be_bytes());
    ip[4..6].copy_from_slice(&0u16.to_be_bytes());
    ip[6..8].copy_from_slice(&DF.to_be_bytes());
    ip[8] = 64;
    ip[9] = proto;
    ip[10] = 0;
    ip[11] = 0;
    ip[12..16].copy_from_slice(src);
    ip[16..20].copy_from_slice(dst);
    let c = checksum::checksum(&ip[..20]);
    ip[10..12].copy_from_slice(&c.to_be_bytes());
}
