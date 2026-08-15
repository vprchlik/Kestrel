//! ARP parse, reply, and a 4-entry cache (D-0040, D-0045).
//!
//! Owns RFC 826 field checks, the wraparound cache, and the drop
//! counters for every rejected shape. Remote bytes never panic: a bad
//! packet increments a counter and is dropped. Check order, each a
//! distinct counter: frame shorter than 14+28 → `drop_short`; htype ≠ 1
//! → `drop_htype`; ptype ≠ 0x0800 → `drop_ptype`; hlen ≠ 6 → `drop_hlen`;
//! plen ≠ 4 → `drop_plen`; opcode ≠ 1 → `drop_op` (replies included);
//! TPA ≠ our IP → `drop_tpa`. The cache is the same code for one
//! gateway as for four; wraparound is exercised at init and then
//! cleared so dummy entries do not shadow 10.0.2.2. Without this
//! module we classify EtherType and never answer, so slirp never
//! learns us.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::println;
use core::ptr::{addr_of, addr_of_mut};

/// Ethernet header length. IEEE 802.3.
const ETH_HDR: usize = 14;
/// ARP payload after the Ethernet header. RFC 826.
const ARP_LEN: usize = 28;
/// Hardware type Ethernet. RFC 826.
const HTYPE_ETH: u16 = 1;
/// Protocol type IPv4.
const PTYPE_IPV4: u16 = 0x0800;
const HLEN: u8 = 6;
const PLEN: u8 = 4;
/// Opcode request. RFC 826.
const OP_REQUEST: u16 = 1;
const CACHE_N: usize = 4;

#[derive(Clone, Copy)]
struct Ent {
    ip: [u8; 4],
    mac: [u8; 6],
}

static mut ENTRIES: [Option<Ent>; CACHE_N] = [None; CACHE_N];
static mut NEXT: usize = 0;

static mut DROP_SHORT: u32 = 0;
static mut DROP_HTYPE: u32 = 0;
static mut DROP_PTYPE: u32 = 0;
static mut DROP_HLEN: u32 = 0;
static mut DROP_PLEN: u32 = 0;
static mut DROP_OP: u32 = 0;
static mut DROP_TPA: u32 = 0;

/// A request for our IP: cache updated; caller transmits a reply.
pub struct RequestForUs {
    pub sha: [u8; 6],
    pub spa: [u8; 4],
}

pub fn drop_short() -> u32 {
    unsafe { DROP_SHORT }
}
pub fn drop_htype() -> u32 {
    unsafe { DROP_HTYPE }
}
pub fn drop_ptype() -> u32 {
    unsafe { DROP_PTYPE }
}
pub fn drop_hlen() -> u32 {
    unsafe { DROP_HLEN }
}
pub fn drop_plen() -> u32 {
    unsafe { DROP_PLEN }
}
pub fn drop_op() -> u32 {
    unsafe { DROP_OP }
}
pub fn drop_tpa() -> u32 {
    unsafe { DROP_TPA }
}

fn be16(b: &[u8], off: usize) -> u16 {
    u16::from_be_bytes([b[off], b[off + 1]])
}

/// Parse an Ethernet frame that already classified as EtherType ARP.
/// `None` = dropped (counter already incremented).
pub fn process(eth: &[u8], our_ip: &[u8; 4]) -> Option<RequestForUs> {
    if eth.len() < ETH_HDR + ARP_LEN {
        unsafe { DROP_SHORT = DROP_SHORT.wrapping_add(1) };
        println!("arp: drop short frame.len={}", eth.len());
        return None;
    }
    let a = &eth[ETH_HDR..ETH_HDR + ARP_LEN];
    let htype = be16(a, 0);
    if htype != HTYPE_ETH {
        unsafe { DROP_HTYPE = DROP_HTYPE.wrapping_add(1) };
        println!("arp: drop htype={htype:#06x}");
        return None;
    }
    let ptype = be16(a, 2);
    if ptype != PTYPE_IPV4 {
        unsafe { DROP_PTYPE = DROP_PTYPE.wrapping_add(1) };
        println!("arp: drop ptype={ptype:#06x}");
        return None;
    }
    if a[4] != HLEN {
        unsafe { DROP_HLEN = DROP_HLEN.wrapping_add(1) };
        println!("arp: drop hlen={}", a[4]);
        return None;
    }
    if a[5] != PLEN {
        unsafe { DROP_PLEN = DROP_PLEN.wrapping_add(1) };
        println!("arp: drop plen={}", a[5]);
        return None;
    }
    let op = be16(a, 6);
    if op != OP_REQUEST {
        unsafe { DROP_OP = DROP_OP.wrapping_add(1) };
        println!("arp: drop op={op}");
        return None;
    }
    let mut sha = [0u8; 6];
    let mut spa = [0u8; 4];
    let mut tpa = [0u8; 4];
    sha.copy_from_slice(&a[8..14]);
    spa.copy_from_slice(&a[14..18]);
    tpa.copy_from_slice(&a[24..28]);
    if &tpa != our_ip {
        unsafe { DROP_TPA = DROP_TPA.wrapping_add(1) };
        println!(
            "arp: drop tpa {}.{}.{}.{}",
            tpa[0], tpa[1], tpa[2], tpa[3]
        );
        return None;
    }
    learn(spa, sha);
    Some(RequestForUs { sha, spa })
}

/// Fill a 60-byte Ethernet frame (42-byte ARP reply padded). Caller
/// prefixes the 12-byte virtio-net header.
pub fn write_reply(eth: &mut [u8], our_mac: &[u8; 6], our_ip: &[u8; 4], req: &RequestForUs) {
    if eth.len() < 60 {
        panic!("arp: reply buf {} < 60", eth.len());
    }
    for b in eth.iter_mut().take(60) {
        *b = 0;
    }
    eth[0..6].copy_from_slice(&req.sha);
    eth[6..12].copy_from_slice(our_mac);
    eth[12..14].copy_from_slice(&0x0806u16.to_be_bytes());
    let a = &mut eth[ETH_HDR..ETH_HDR + ARP_LEN];
    a[0..2].copy_from_slice(&HTYPE_ETH.to_be_bytes());
    a[2..4].copy_from_slice(&PTYPE_IPV4.to_be_bytes());
    a[4] = HLEN;
    a[5] = PLEN;
    a[6..8].copy_from_slice(&2u16.to_be_bytes()); // reply
    a[8..14].copy_from_slice(our_mac);
    a[14..18].copy_from_slice(our_ip);
    a[18..24].copy_from_slice(&req.sha);
    a[24..28].copy_from_slice(&req.spa);
}

fn learn(ip: [u8; 4], mac: [u8; 6]) {
    for i in 0..CACHE_N {
        let slot = unsafe { core::ptr::read(addr_of!(ENTRIES[i])) };
        if let Some(mut e) = slot {
            if e.ip == ip {
                e.mac = mac;
                unsafe { core::ptr::write(addr_of_mut!(ENTRIES[i]), Some(e)) };
                return;
            }
        }
    }
    let i = unsafe { NEXT };
    unsafe {
        core::ptr::write(addr_of_mut!(ENTRIES[i]), Some(Ent { ip, mac }));
        NEXT = (i + 1) % CACHE_N;
    }
}

pub fn lookup(ip: [u8; 4]) -> Option<[u8; 6]> {
    for i in 0..CACHE_N {
        if let Some(e) = unsafe { core::ptr::read(addr_of!(ENTRIES[i])) } {
            if e.ip == ip {
                return Some(e.mac);
            }
        }
    }
    None
}

fn clear() {
    for i in 0..CACHE_N {
        unsafe { core::ptr::write(addr_of_mut!(ENTRIES[i]), None) };
    }
    unsafe { NEXT = 0 };
}

/// Five distinct inserts into four slots; oldest gone; then clear (D-0045).
pub fn wrap_selftest() {
    for i in 1u8..=5 {
        learn([10, 0, 0, i], [0, 0, 0, 0, 0, i]);
    }
    if lookup([10, 0, 0, 1]).is_some() {
        panic!("arp: wrap did not evict 10.0.0.1");
    }
    for i in 2u8..=5 {
        match lookup([10, 0, 0, i]) {
            Some(m) if m[5] == i => {}
            _ => panic!("arp: wrap missing 10.0.0.{i}"),
        }
    }
    clear();
    if lookup([10, 0, 0, 5]).is_some() {
        panic!("arp: wrap selftest left dummy entries");
    }
    println!("ARP CACHE WRAP OK");
}
