//! SBI ecall wrapper: the only path from S-mode into OpenSBI.
//!
//! Owns the calling convention (EID in `a7`, FID in `a6`, args in `a0..a5`,
//! `ecall`; error in `a0`, value in `a1` on return), DBCN console-byte, and
//! SRST shutdown. Without this module the kernel cannot talk to firmware.

use core::arch::asm;

/// SBI success. Spec: Binary Encoding, error codes.
pub const SBI_SUCCESS: isize = 0;
/// Extension or function not available.
pub const SBI_ERR_NOT_SUPPORTED: isize = -2;

/// Base extension. SBI spec ch. 4, EID `0x10`.
pub const EID_BASE: usize = 0x10;
/// `sbi_probe_extension`. SBI spec ch. 4, FID 3.
pub const FID_BASE_PROBE: usize = 3;

/// Debug Console extension. SBI spec, EID ASCII `"DBCN"`.
pub const EID_DBCN: usize = 0x4442_434E;
/// `sbi_debug_console_write_byte`. SBI spec DBCN, FID 2.
pub const FID_DBCN_WRITE_BYTE: usize = 2;

/// System Reset extension. SBI spec, EID ASCII `"SRST"`.
#[cfg_attr(feature = "panic-selftest", allow(dead_code))]
pub const EID_SRST: usize = 0x5352_5354;
/// `sbi_system_reset`. SBI spec SRST, FID 0.
#[cfg_attr(feature = "panic-selftest", allow(dead_code))]
pub const FID_SRST_RESET: usize = 0;
/// Shutdown. SBI spec SRST, `reset_type`.
#[cfg_attr(feature = "panic-selftest", allow(dead_code))]
pub const SRST_TYPE_SHUTDOWN: usize = 0;
/// No recorded reason. SBI spec SRST, `reset_reason`.
#[cfg_attr(feature = "panic-selftest", allow(dead_code))]
pub const SRST_REASON_NONE: usize = 0;

/// Return of an SBI call: `a0` = error, `a1` = value.
#[derive(Clone, Copy)]
pub struct Sbiret {
    pub error: isize,
    pub value: usize,
}

/// Issue `ecall`. SBI spec, Binary Encoding: EID in `a7`, FID in `a6`,
/// arguments in `a0..a5`. On return `a0` is the error code and `a1` the
/// value; every other register is preserved by the SBI implementation.
#[inline]
fn ecall(eid: usize, fid: usize, arg0: usize, arg1: usize, arg2: usize) -> Sbiret {
    let mut error: isize;
    let mut value: usize;
    unsafe {
        asm!(
            "ecall",
            inout("a0") arg0 => error,
            inout("a1") arg1 => value,
            in("a2") arg2,
            in("a6") fid,
            in("a7") eid,
            options(nostack),
        );
    }
    Sbiret { error, value }
}

/// Console is missing or a DBCN call failed. Leave the SBI error in `a0`,
/// `ebreak` so QEMU `-d int` / GDB see a breakpoint, then park.
///
/// Must not call `panic!`: the panic handler will want the console, and a
/// failing `putchar` must not recurse. There is no other output channel yet.
pub fn abort_sbi(error: isize) -> ! {
    unsafe {
        asm!(
            "ebreak",
            "1: wfi",
            "j 1b",
            in("a0") error,
            options(noreturn),
        );
    }
}

/// Probe an extension via BASE. `value == 0` means absent; nonzero means
/// present. A nonzero `error` means the probe itself failed.
pub fn probe_extension(eid: usize) -> Sbiret {
    ecall(EID_BASE, FID_BASE_PROBE, eid, 0, 0)
}

/// Abort if DBCN is missing. No fallback to legacy putchar (D-0015).
pub fn require_dbcn() {
    let ret = probe_extension(EID_DBCN);
    if ret.error != SBI_SUCCESS {
        abort_sbi(ret.error);
    }
    if ret.value == 0 {
        abort_sbi(SBI_ERR_NOT_SUPPORTED);
    }
}

/// Write one byte through DBCN FID 2. Aborts on any SBI error.
pub fn console_write_byte(byte: u8) {
    let ret = ecall(EID_DBCN, FID_DBCN_WRITE_BYTE, byte as usize, 0, 0);
    if ret.error != SBI_SUCCESS {
        abort_sbi(ret.error);
    }
}

/// Ask OpenSBI to shut the machine down. Does not return on success.
///
/// Probes SRST first (same BASE probe as DBCN). If the extension is absent
/// or `ecall` returns, returns that `Sbiret` so the caller can print and
/// park — never fall off into whatever follows `call kmain`.
#[cfg_attr(feature = "panic-selftest", allow(dead_code))]
pub fn shutdown() -> Sbiret {
    let probe = probe_extension(EID_SRST);
    if probe.error != SBI_SUCCESS {
        return probe;
    }
    if probe.value == 0 {
        return Sbiret {
            error: SBI_ERR_NOT_SUPPORTED,
            value: 0,
        };
    }
    ecall(
        EID_SRST,
        FID_SRST_RESET,
        SRST_TYPE_SHUTDOWN,
        SRST_REASON_NONE,
        0,
    )
}
