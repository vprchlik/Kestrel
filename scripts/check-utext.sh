#!/usr/bin/env bash
# Guard .utext against references into kernel .text / .rodata.
#
# The failure is a *symbol* that resolves outside user sections and the
# task stack/break windows — not a particular opcode. `auipc+addi` and
# `lui+addi` that form a user address are legitimate (a real `write` needs
# a buffer in `.urodata`). A `lui`/`li` immediate used as a *value* (T2.7
# passing a kernel address to `write`) is not a reference and is allowed.
# `gp`/`tp` as an addressing base is still rejected: those registers are
# kernel-owned (D-0032).
set -euo pipefail

KERNEL="${1:-target/riscv64gc-unknown-none-elf/debug/whimbrel}"
if [ ! -f "$KERNEL" ]; then
    echo "check-utext: no kernel at $KERNEL (build first)" >&2
    exit 1
fi

HOST="$(rustc -vV | awk '/host:/ {print $2}')"
SYSROOT="$(rustc --print sysroot)"
BIN="${SYSROOT}/lib/rustlib/${HOST}/bin"
OBJDUMP="${BIN}/llvm-objdump"
NM="${BIN}/llvm-nm"

if [ ! -x "$OBJDUMP" ] || [ ! -x "$NM" ]; then
    echo "check-utext: llvm-objdump/llvm-nm not under $BIN" >&2
    exit 1
fi

exec python3 - "$OBJDUMP" "$NM" "$KERNEL" <<'PY'
import re, subprocess, sys

objdump, nm, kernel = sys.argv[1], sys.argv[2], sys.argv[3]

dump = subprocess.check_output([objdump, "-d", "--section=.utext", kernel], text=True)
syms_txt = subprocess.check_output([nm, kernel], text=True)

def parse_syms(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]+)\s+\S\s+(\S+)$", line.strip())
        if m:
            out[m.group(2)] = int(m.group(1), 16)
    return out

syms = parse_syms(syms_txt)
need = [
    "__utext_start", "__utext_end",
    "__urodata_start", "__urodata_end",
    "__udata_start", "__udata_end",
    "__ubss_start", "__ubss_end",
]
for n in range(4):
    need += [
        f"__ustack{n}_bottom", f"__ustack{n}_top",
        f"__ubrk{n}_base", f"__ubrk{n}_wall",
    ]
missing = [s for s in need if s not in syms]
if missing:
    print("check-utext FAIL: missing symbols:", ", ".join(missing), file=sys.stderr)
    sys.exit(1)

ranges = [
    (".utext",    syms["__utext_start"],   syms["__utext_end"]),
    (".urodata",  syms["__urodata_start"], syms["__urodata_end"]),
    (".udata",    syms["__udata_start"],   syms["__udata_end"]),
    (".ubss",     syms["__ubss_start"],    syms["__ubss_end"]),
]
for n in range(4):
    ranges.append((f"ustack{n}", syms[f"__ustack{n}_bottom"], syms[f"__ustack{n}_top"]))
    ranges.append((f"break{n}",  syms[f"__ubrk{n}_base"],     syms[f"__ubrk{n}_wall"]))

def where(addr):
    for name, lo, hi in ranges:
        if lo < hi and lo <= addr < hi:
            return name
    return None

# Kernel .text / .rodata sit below .utext. Anything that resolves there
# is the bug this check exists to catch.
kernel_lo = 0x80200000
kernel_hi = syms["__utext_start"]

def fail(pc, insn, reason):
    print(f"check-utext FAIL at {pc:#x}: {insn}", file=sys.stderr)
    print(f"  {reason}", file=sys.stderr)
    sys.exit(1)

# llvm-objdump: "80228000: 00100513     	li	a0, 0x1"
line_re = re.compile(
    r"^\s*([0-9a-fA-F]+):\s+(?:[0-9a-fA-F]{2,8}\s+)*\s*(\S+)(?:\s+(.*))?$"
)

insns = []
for line in dump.splitlines():
    m = line_re.match(line)
    if not m:
        continue
    pc = int(m.group(1), 16)
    op = m.group(2)
    args = (m.group(3) or "").strip()
    insns.append((pc, op, args, line.strip()))

GP_TP = re.compile(r"(?:^|[^a-z0-9])(gp|tp)(?:[^a-z0-9]|$)")
LOAD_STORE = {
    "lb", "lh", "lw", "ld", "lbu", "lhu", "lwu",
    "sb", "sh", "sw", "sd",
    "flw", "fld", "fsw", "fsd",
}
# Arithmetic / branches / values: not link-time symbol references.
SKIP = {
    "lui", "li", "addi", "addiw", "add", "sub", "ecall", "ebreak",
    "unimp", "nop", "mv", "neg", "not", "seqz", "snez", "sltz",
    "sgtz", "beq", "bne", "blt", "bge", "bltu", "bgeu",
    "beqz", "bnez", "blez", "bgez", "bltz", "bgtz",
    "slli", "srli", "srai", "slliw", "srliw", "sraiw",
    "and", "or", "xor", "andi", "ori", "xori",
    "sll", "srl", "sra", "sllw", "srlw", "sraw",
    "addw", "subw",
}


def parse_reg_imm(args):
    parts = [p.strip() for p in args.split(",")]
    if len(parts) != 2:
        return None
    rd, imm = parts[0], parts[1]
    try:
        return rd, int(imm, 0)
    except ValueError:
        return None


def parse_mem(args):
    m = re.match(r"([^,]+),\s*(-?(?:0x)?[0-9a-fA-F]+)\(([a-z0-9]+)\)", args)
    if not m:
        return None
    return m.group(1).strip(), int(m.group(2), 0), m.group(3)


def parse_jalr(args):
    parts = [p.strip() for p in args.split(",") if p.strip()]
    if len(parts) == 1:
        return "ra", parts[0], 0
    if len(parts) == 3:
        try:
            return parts[0], parts[1], int(parts[2], 0)
        except ValueError:
            return None
    if len(parts) == 2:
        try:
            return "ra", parts[0], int(parts[1], 0)
        except ValueError:
            return None
    return None


def hex_targets(args):
    return [int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]+", args)]


def check_addr(pc, insn, addr, how):
    if where(addr) is not None:
        return
    if kernel_lo <= addr < kernel_hi:
        fail(
            pc,
            insn,
            f"{how} {addr:#x} resolves into kernel .text/.rodata "
            f"[{kernel_lo:#x}, {kernel_hi:#x})",
        )
    fail(
        pc,
        insn,
        f"{how} {addr:#x} is outside user sections and every "
        f"task's stack/break window",
    )


if not insns:
    print("check-utext FAIL: .utext is empty or missing", file=sys.stderr)
    sys.exit(1)

paired = set()
for i, (pc, op, args, raw) in enumerate(insns):
    if i in paired:
        continue
    if GP_TP.search(args) or GP_TP.search(op):
        fail(pc, raw, "gp/tp used from .utext (kernel-owned; D-0032)")

    if op in SKIP:
        continue

    if op in LOAD_STORE:
        parsed = parse_mem(args)
        if parsed is None:
            fail(pc, raw, f"could not parse {op} operands")
        # Runtime bases (sp, aN, …) are not link-time symbol references.
        continue

    if op in ("jal", "j", "call", "tail", "c.j", "c.jal"):
        ts = hex_targets(args)
        if not ts:
            fail(pc, raw, "control transfer with no resolvable target")
        check_addr(pc, raw, ts[-1], op)
        continue

    if op in ("la", "lla"):
        ts = hex_targets(args)
        if not ts:
            fail(pc, raw, f"{op} with no resolvable address")
        check_addr(pc, raw, ts[-1], op)
        continue

    if op == "auipc":
        parsed = parse_reg_imm(args)
        if parsed is None:
            fail(pc, raw, "could not parse auipc")
        rd, imm = parsed
        auipc_val = (pc + (imm << 12)) & 0xFFFFFFFFFFFFFFFF
        target = auipc_val
        how = "auipc"
        if i + 1 < len(insns):
            _pc2, op2, args2, _raw2 = insns[i + 1]
            used = False
            if op2 in ("addi", "addiw"):
                parts = [p.strip() for p in args2.split(",")]
                if len(parts) == 3 and parts[1] == rd:
                    try:
                        target = (auipc_val + int(parts[2], 0)) & 0xFFFFFFFFFFFFFFFF
                        how = "auipc+addi"
                        used = True
                    except ValueError:
                        pass
            elif op2 in LOAD_STORE:
                mem = parse_mem(args2)
                if mem and mem[2] == rd:
                    target = (auipc_val + mem[1]) & 0xFFFFFFFFFFFFFFFF
                    how = f"auipc+{op2}"
                    used = True
            elif op2 == "jalr":
                j = parse_jalr(args2)
                if j and j[1] == rd:
                    target = (auipc_val + j[2]) & 0xFFFFFFFFFFFFFFFF
                    how = "auipc+jalr"
                    used = True
            if used:
                paired.add(i + 1)
        check_addr(pc, raw, target, how)
        continue

    if op in ("jalr", "c.jr", "c.jalr"):
        # Bare register jump: cannot resolve statically. Fail closed unless
        # it was the auipc pair already consumed above (we only look ahead
        # from auipc, so a lone jalr still lands here).
        fail(pc, raw, "unresolved jalr in .utext")

    fail(pc, raw, f"unhandled {op} in .utext")

print("check-utext OK")
PY
