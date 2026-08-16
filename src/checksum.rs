//! Internet checksum (RFC 1071).
//!
//! Owns the one's-complement sum used by IPv4 and ICMP. Words are
//! assembled with `from_be_bytes` because the hart is little-endian;
//! overlaying `u16` on packet memory would swap every field. Carries
//! fold **until the high half is zero**, not once: `0x1ffff` becomes
//! `0x10000` after one pass, which is still a carry. An odd trailing
//! byte is padded on the right (low 8 bits zero) as RFC 1071 specifies.
//! Without this module a well-formed slirp packet would still be
//! accepted with the checksum skipped — the dishonest skip.

#![cfg_attr(
    any(feature = "panic-selftest", feature = "hang-selftest"),
    allow(dead_code)
)]

use crate::println;

/// Fold carries until `sum` fits in 16 bits. RFC 1071.
pub fn fold(mut sum: u32) -> u16 {
    while sum > 0xffff {
        sum = (sum & 0xffff) + (sum >> 16);
    }
    sum as u16
}

/// One's-complement sum of `data` as 16-bit big-endian words. Does not
/// fold; callers fold once at the end so a pseudo-header and a UDP
/// datagram can share one accumulator.
pub fn accumulate(mut acc: u32, data: &[u8]) -> u32 {
    let mut i = 0;
    while i + 1 < data.len() {
        acc = acc.wrapping_add(u16::from_be_bytes([data[i], data[i + 1]]) as u32);
        i += 2;
    }
    if i < data.len() {
        // Odd length: last byte is the high half of a padded word.
        acc = acc.wrapping_add((data[i] as u32) << 8);
    }
    acc
}

/// One's-complement sum of `data` as 16-bit big-endian words.
pub fn sum(data: &[u8]) -> u16 {
    fold(accumulate(0, data))
}

/// Value to store in a checksum field: the complement of `sum`, with
/// the field itself zeroed in `data`.
pub fn checksum(data: &[u8]) -> u16 {
    !sum(data)
}

/// A header or message is valid iff its one's-complement sum is all ones.
pub fn valid(data: &[u8]) -> bool {
    sum(data) == 0xffff
}

/// Fold-until-stable, odd-length pad, and a built header that verifies.
pub fn selftest() {
    // One fold of 0x1ffff is 0x10000; as u16 that is 0. We need 1.
    if fold(0x1ffff) != 1 {
        panic!("checksum: fold did not run until stable (0x1ffff)");
    }
    if sum(&[0x01]) != 0x0100 {
        panic!("checksum: odd byte 0x01 should pad to 0x0100");
    }
    if sum(&[0x00, 0x01]) != 0x0001 {
        panic!("checksum: word 0x0001");
    }
    if sum(&[0xff, 0xff, 0x00, 0x01]) != 0x0001 {
        panic!("checksum: 0xffff + 1 should fold to 1");
    }
    let mut hdr = [0u8; 20];
    hdr[0] = 0x45;
    hdr[2] = 0x00;
    hdr[3] = 0x14;
    hdr[8] = 64;
    hdr[9] = 1;
    hdr[12..16].copy_from_slice(&[10, 0, 2, 15]);
    hdr[16..20].copy_from_slice(&[10, 0, 2, 2]);
    let c = checksum(&hdr);
    hdr[10..12].copy_from_slice(&c.to_be_bytes());
    if !valid(&hdr) {
        panic!("checksum: built IPv4 header did not verify");
    }
    hdr[9] ^= 1;
    if valid(&hdr) {
        panic!("checksum: mutated header still verified");
    }
    println!("CHECKSUM OK");
}
