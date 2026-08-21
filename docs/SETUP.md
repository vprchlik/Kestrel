# SETUP — local development environment

Target host: Linux (Debian/Ubuntu commands shown; anything that can run QEMU
and rustup works — macOS notes at the bottom). Everything in §1–§2 is also
automated in `scripts/install.sh` — if you change one, change both. The
dedicated measurement host in §7 is **not** what install.sh provisions;
report-grade numbers never come from a build VM (D-0055).

## 1. Host packages

```bash
sudo apt-get update
# RISC-V QEMU: Ubuntu 26.04 split it out of qemu-system-misc into
# qemu-system-riscv. 24.04/22.04 still ship qemu-system-riscv64 from
# qemu-system-misc. Prefer the new package when the archive has it.
if apt-cache show qemu-system-riscv >/dev/null 2>&1; then
    QEMU_PKG=qemu-system-riscv
else
    QEMU_PKG=qemu-system-misc
fi
sudo apt-get install -y \
    "$QEMU_PKG" \             # provides qemu-system-riscv64 (the whole "hardware")
    gdb-multiarch \           # GDB built with riscv64 support (Debian/Ubuntu naming)
    build-essential \         # linker/binutils/make for build scripts and C-adjacent tooling
    tshark \                  # pcap assertions in the M3+ net harness (filter-dump captures)
    curl git
```

**Package split:** on Ubuntu 26.04 (`resolute`), `qemu-system-riscv64` lives
in `qemu-system-riscv` ("QEMU full system emulation binaries (riscv)").
Installing only `qemu-system-misc` on that release does **not** put the
binary on PATH. Ubuntu 24.04 and 22.04 still ship it from
`qemu-system-misc`; the `apt-cache show` test above picks the right
package so an older host keeps working. After install,
`command -v qemu-system-riscv64` must succeed.

Version note: any QEMU ≥ 7.x is fine (Ubuntu 22.04+ ships qemu ≥ 6.2 which
also works). The exact version in use gets recorded in the M4 report for
reproducibility: `qemu-system-riscv64 --version` plus the binary hash the
bench harness records.

M3 note: from T3.1 every QEMU invocation (`just run`, `just test`,
`just panic`, `just debug`, and `scripts/boot-test.sh`) carries
`-global virtio-mmio.force-legacy=false` (modern virtio-mmio, D-0038)
and a `virtio-net-device` on `-netdev user,id=net0` (D-0039: a netless
boot is a misconfigured harness). From T3.4 every invocation also
carries `-object filter-dump,id=f0,netdev=net0,file=whimbrel.pcap`
(D-0043: capture is standing infrastructure). From T3.5 every
invocation also carries `hostfwd=tcp::8080-:80` on that netdev — a host
TCP connect to 127.0.0.1:8080 is what makes slirp ARP for 10.0.2.15.
From T3.8 every invocation also carries `hostfwd=udp::7777-:7` (UDP
echo on guest port 7). `tshark` above is what the harness uses to assert
on those captures — without it, `just test` fails on a machine that has
everything else. On Ubuntu 26.04+ the binary can be present and still
fail every pcap read: §7 AppArmor. Kernel / Buildroot builds on 26.04
also hit §7's uutils `/usr/bin/install` trap (`install --version`
prints `uutils`; GNU is `/usr/bin/gnuinstall`).

## 2. Rust toolchain

Install rustup from <https://rustup.rs> if not present, then (from the repo
root, where `rust-toolchain.toml` pins the channel):

```bash
rustup target add riscv64gc-unknown-none-elf     # precompiled core/alloc for our bare-metal triple
rustup component add llvm-tools-preview rust-src # llvm-tools: objdump/nm/size via cargo; rust-src: rust-analyzer + any future build-std
cargo install cargo-binutils just                # cargo objdump/nm/size wrappers; just = task runner (see justfile)
```

(`rust-toolchain.toml` requests the target and components automatically on
first `cargo build`; the explicit commands above make failures visible early.)

## 3. Verification

**3a. QEMU + OpenSBI alone (no kernel):**

```bash
qemu-system-riscv64 -machine virt -nographic -bios default
```

Expected: the OpenSBI banner within a second —

```
OpenSBI v1.x
   ____                    _____ ____ _____
  / __ \                  / ____|  _ \_   _|
 | |  | |_ __   ___ _ __ | (___ | |_) || |
 ...
Platform Name             : riscv-virtio,qemu
...
Domain0 Next Address      : 0x0000000000000000
```

then silence (there is no kernel to jump to). **Exit with `Ctrl-a x`.**
(`Ctrl-a` is QEMU's escape character in `-nographic`; `Ctrl-a c` toggles the
monitor, `Ctrl-a h` lists the rest.)

**3b. Toolchain + scaffold:**

```bash
just build   # cross-compiles the scaffold kernel
just test    # boots it headless, asserts on kernel marker
```

Expected: `TEST PASS: found "M3 UNIKERNEL OK"` and exit 0. `just test-panic`
exits 1; `just test-hang` exits 2. `just run-http` boots the persist image;
`curl -v http://127.0.0.1:8080/` from another terminal should get HTTP 200
and body `whimbrel`.

**3c. Debug loop:** `just debug` in one terminal (QEMU frozen, GDB stub on
:1234), `just gdb` in another — you should land at a `(gdb)` prompt showing
the remote target. Quit gdb, then `Ctrl-a x` the QEMU terminal.

## 4. Editor extensions (Cursor / VS Code)

Why each matters *for bare-metal Rust specifically*:

| Extension (id) | Why it matters here |
|---|---|
| **rust-analyzer** (`rust-lang.rust-analyzer`) | The Rust language server. Must be told we cross-compile (see `.vscode/settings.json`: `cargo.target` = the bare-metal triple, `check.allTargets` = false) or it checks against the host target and floods a `no_std` project with false `can't find crate std` errors. |
| **CodeLLDB** (`vadimcn.vscode-lldb`) | Debugger UI; its `custom` launch mode can attach to QEMU's GDB stub (`.vscode/launch.json`), giving breakpoints/stepping in the editor with one keypress. For CSRs and disassembly-truth, drop to `gdb-multiarch` (see DEBUGGING.md §1). |
| **Even Better TOML** (`tamasfe.even-better-toml`) | Schema-aware editing for the unusually load-bearing TOML in this repo: `.cargo/config.toml`, `rust-toolchain.toml`, `Cargo.toml` — a typo in the first two fails at *link or boot* time, not compile time; catching it at edit time is worth an extension. |
| **RISC-V Support** (`zhwu95.riscv`) | Syntax highlighting for RISC-V assembly — we hand-write the entry stub, trap vectors, and context switch, and read a lot of `objdump` output. Any RISC-V asm highlighter works; this is the common pick. |
| **Error Lens** (`usernamehw.errorlens`) | Renders diagnostics inline at the offending line. In `no_std` + `unsafe`-adjacent code, rust-analyzer's hints (unused `unsafe`, wrong ABI strings, feature gates) deserve zero-friction visibility. |
| **Hex Editor** (`ms-vscode.hexeditor`) | Inspect raw binaries: the kernel image, DTB dumps, virtqueue memory dumps. Occasionally the question is literally "what bytes are at this offset" and this answers it in-editor. |

**Marketplace note:** Cursor installs extensions from the Open VSX registry,
not Microsoft's marketplace. Most of the list is published there, but some
Microsoft-licensed extensions (notably **Hex Editor**, sometimes others) may
be missing or lag behind — if an install fails, download the `.vsix` from the
publisher's GitHub releases (or the VS Code marketplace via browser) and use
"Extensions: Install from VSIX…" in the command palette. Check each
extension's license permits use outside Microsoft products; for Hex Editor
(MIT-licensed, open source) this is fine.

## 5. macOS notes (if developing there)

`brew install qemu just` (QEMU ≥ 7 includes riscv64 + bundled OpenSBI);
`brew install riscv64-elf-gdb` for the debugger (adjust the `just gdb` recipe
or add a `gdb := env_var_or_default(...)` override); rustup steps identical.
CI/benchmarks (M4) should still run on Linux for comparability.

## 6. Build VM

`scripts/install.sh` provisions a build VM with the same toolchain as
§1–§2, so that machine can compile and boot-test. Keep the script in
lockstep with those sections. A build VM is **not** the bench host
(D-0055): it may be a KVM guest with no cpufreq. Gates run there;
report-grade numbers never come from one.

## 7. Dedicated measurement host (D-0055)

Development and `just test` run anywhere QEMU works. **Every number in the
M4 report** comes from one dedicated Ubuntu machine that meets the checks
below. T4.2 stamps from a KVM pod are ladder-ordering only. On Ubuntu
26.04+ extra host steps sit outside `scripts/install.sh` (cloud agents
never run `just linux-build`): the QEMU package split in §1, the
**uutils `/usr/bin/install` shim** below, the tshark AppArmor override,
and the `just linux-build` packages.

### What the machine must be

| Check | Passes when | How to verify by hand |
|---|---|---|
| Not a VM | `systemd-detect-virt` prints `none` | `systemd-detect-virt`; any other string (kvm, qemu, microsoft, …) or a missing binary is a fail |
| cpufreq present | `cpu0` has a scaling governor file | `test -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor` |
| performance governor | every **online** CPU is `performance` | `cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor` — no `schedutil` / `powersave` / mixed |
| SMT off | no sibling threads | `/sys/devices/system/cpu/smt/control` is `off`, or `smt/active` is `0`; if those nodes are absent, each `thread_siblings_list` is a single CPU |
| turbo off | the frequency-boost knob is disabled | Intel: `/sys/devices/system/cpu/intel_pstate/no_turbo` is `1`. AMD: `/sys/devices/system/cpu/cpufreq/boost` is `0`. Neither interface present → fail (cannot prove turbo is off) |
| steal 0 across a batch | every trial in the batch, warmup included, has `steal_ticks=0` | harness `/proc/stat` column; a single nonzero tick fails the batch |

Missing evidence is a fail, not a skip. `governor=unavailable` is a fail
on this host, not a recorded curiosity.

The provisioned machine (Ubuntu 26.04, Ryzen 7 7800X3D, 8 cores SMT off,
boost off, QEMU 10.2.1, steal 0) is the source of the turbo-off numbers
in D-0055. Copy the harness machine-spec block into the report, not this
sentence.

### Host packages for `just linux-build`

§1's `build-essential` is not enough. A fresh 26.04 following only that
list dies in Buildroot's `dependencies.sh` / kernel `scripts/kconfig`
before it produces an Image. Install, then leave them installed:

```bash
sudo apt-get install -y \
    bison \          # kernel scripts/kconfig (YACC)
    flex \           # kernel scripts/kconfig (LEX)
    libssl-dev \     # kernel host headers (module signing / scripts)
    bc               # kernel Makefile timeconst; recipe preflight
```

This host needed `bison`, `flex`, and `libssl-dev` on top of an already
working `just test` machine. `bc` was already present; it is listed
because the recipe fail-closes without it.

**QEMU package split** is in §1: on 26.04 `qemu-system-riscv64` lives in
`qemu-system-riscv`, not `qemu-system-misc`. `install.sh` and the
`apt-cache show` test in §1 pick the right package; `command -v
qemu-system-riscv64` must succeed after install.

### Ubuntu 26.04: uutils `/usr/bin/install` (Buildroot / kernel)

Ubuntu 26.04 (`resolute`) ships **uutils** as `/usr/bin/install`, not
GNU coreutils. Confirm with `install --version`: a first line containing
`uutils` (this host: 0.8.0) is the broken one. Buildroot 2026.02.3's
`support/dependencies/dependencies.sh` hard-fails on that binary
(uutils#12166 — GNU `install` flags the kernel and Buildroot rely on).
The failure looks like a missing host compiler or a bad `PATH`, not like
a coreutils fork.

GNU `install` is already on this image as `/usr/bin/gnuinstall`
(`gnuinstall --version` must print GNU coreutils). `scripts/linux-build.sh`
prepends a directory with `install → gnuinstall` to `PATH` for the
Buildroot invocation. It does **not** run `update-alternatives` or
otherwise rewrite the system `/usr/bin/install`. If `gnuinstall` is
missing, install GNU `coreutils` so that binary exists; do not replace
the uutils alternative globally on a desktop that expects it.

Search terms that should land here: `uutils`, `/usr/bin/install`,
`gnuinstall`, Buildroot `dependencies.sh`. Same class of 26.04-only
host trap as the tshark AppArmor profile below — not a Whimbrel kernel
bug, and not something `scripts/install.sh` should change (cloud agents
do not run `just linux-build`).

### Required 26.04+ step: tshark AppArmor override

Ubuntu 26.04 ships an **enforcing** AppArmor profile for `/usr/bin/tshark`
that can read pcaps under `/tmp` (the `user-tmp` abstraction) but not
under a home-directory clone. The harness writes `whimbrel.pcap` in the
repo and `results/trials/.../qemu.pcap` under the same tree; a clone
under `$HOME` therefore fails every pcap assert after a green build and
boot:

```
TEST FAIL: tshark could not read whimbrel.pcap (status=3)
tshark: You don't have permission to read the file "whimbrel.pcap".
```

`dmesg` / journal: `apparmor="DENIED" profile="tshark" name="…/whimbrel.pcap"`.
The file itself is world-readable; Python can open it. The same binary
reads a copy of that pcap in `/tmp` fine — that is the diagnostic, not
the fix. Do not relocate the capture; QEMU and the asserts agree on a
tree-local path.

Add a **local** override, read-only, scoped to this clone — not a `$HOME`
grant. Host config, not this tree. `/etc/apparmor.d/tshark` already has
`include if exists <local/tshark>`. Older Ubuntu that never confined
tshark does not need this step.

```bash
# Substitute the clone path if it is not /home/victor/src/Whimbrel.
sudo tee /etc/apparmor.d/local/tshark >/dev/null <<'EOF'
# Whimbrel bench host (Ubuntu 26.04+): tshark -r on filter-dump pcaps.
owner /home/victor/src/Whimbrel/**.pcap r,
owner /home/victor/src/Whimbrel/**.pcapng r,
EOF
sudo apparmor_parser -r /etc/apparmor.d/tshark
tshark -r whimbrel.pcap -c 1   # from the repo root; must print a frame, not a permission error
```

`owner` matches the QEMU-written capture (same uid as the harness). A
package upgrade of `tshark` does not clobber `local/tshark`.

### Measurement shell

Run measurement — and `just test` when you need `check-utext` to see
the kernel — from a **plain login shell**. Cursor's agent shell injects
`CARGO_TARGET_DIR=/tmp/cursor-sandbox-cache/…`, so the image lands
outside the tree and `check-utext` reports `no kernel at
target/riscv64gc-unknown-none-elf/…`. That is an agent-shell artifact,
not a distro bug. Unset `CARGO_TARGET_DIR` if you must use that shell.
See DEBUGGING.md §7.

### What persists across reboot

| Check | Persists? |
|---|---|
| `systemd-detect-virt` = none | Yes — property of the machine |
| cpufreq present | Yes — kernel + CPU, given the same kernel config |
| steal = 0 | Yes on native hardware (no hypervisor steal). Still asserted every batch: it is the proof we are still that machine |
| performance governor | **No.** `power-profiles-daemon` may store `Profile=performance`, but a desktop power-menu click flips it back to Balanced. A sysfs write this boot does not survive reboot unless a service or cmdline (`cpufreq.default_governor=performance`) sets it |
| SMT off | **No** for a sysfs write to `smt/control`. Persists if BIOS disables SMT or the cmdline has `nosmt` |
| turbo off | **No** for a sysfs write (`intel_pstate/no_turbo` or `cpufreq/boost`). Persists with cmdline `intel_pstate=no_turbo` (Intel) or the distro equivalent for AMD |

Treat governor, SMT, and boost as volatile. Re-apply before a batch
(AMD `amd-pstate-epp` host shown; Intel uses `intel_pstate/no_turbo`
instead of `cpufreq/boost`):

```bash
powerprofilesctl set performance
sudo bash -c 'echo off > /sys/devices/system/cpu/smt/control; echo 0 > /sys/devices/system/cpu/cpufreq/boost'
```

On this AMD host, `powerprofilesctl set performance` also sets
`energy_performance_preference=performance`. Re-verify after every
reboot; do not assume.

### What `scripts/bench.py` asserts (fail closed)

At batch start the harness aborts with
`TEST FAIL: host control <name>=… (want …)` unless all of the rows
below hold. Governor is every **online** CPU's `scaling_governor`,
not `cpu0` alone — mixed CPUs fail closed as `mixed:cpuN=…`. A
nonzero per-trial `steal_ticks` aborts at that trial (warmup
included) and again at summarize. Missing sysfs is `unavailable`,
not a pass. No `--allow-dirty`-style override exists for these
checks. Gates (`just test`) do not run this gate.

| Control | Want |
|---|---|
| `governor` (every online CPU `scaling_governor`) | `performance` |
| `smt_control` | `off` |
| `cpufreq_boost` | `0` |
| `virt` (`systemd-detect-virt`) | `none` |
| `steal_start_ticks` (`/proc/stat` at batch start) | `0` |
| `steal_ticks` (per-trial `/proc/stat` steal delta, warmup included) | `0` |

The first five are copied onto every `runs.csv` row (`governor`
records the unanimous `performance` when the check passes) so a
violating batch is identifiable after the fact. Per-trial
`steal_ticks` is measured per boot and fail-closed at 0, warmup
included. USER_HZ=100 ⇒ 10 ms/tick, so `steal_ticks=0` is
necessary, not sufficient. The stability criterion is unchanged
(two interleaved 30-trial batches, max(2%, 200 µs)); these host
checks are additional and do not widen it.

### Machine-spec block (report methodology)

Record once per host, and re-record if any field changes, in the report's
methodology section (not typed from memory — copy from the batch header
the harness writes):

```
nproc:                 <os.cpu_count()>
cpu_model:             </proc/cpuinfo "model name">
host_kernel:           <uname -r>
systemd-detect-virt:   none
cpufreq:               present
governor:              performance (all online CPUs)
smt:                   off
turbo:                 off (<which sysfs node and value>)
qemu_version:          <first line of qemu-system-riscv64 --version>
qemu_hash:             <sha256 of that binary>
steal_ticks:           0 on every trial of the batch
```

`taskset` pins (QEMU vs client cores) stay as D-0055 already requires.
