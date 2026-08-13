# SETUP — local development environment

Target host: Linux (Debian/Ubuntu commands shown; anything that can run QEMU
and rustup works — macOS notes at the bottom). Everything here is also
automated in `scripts/install.sh`, which `.cursor/environment.json` uses to
provision cloud agents — if you change one, change both.

## 1. Host packages

```bash
sudo apt-get update
sudo apt-get install -y \
    qemu-system-misc \        # provides qemu-system-riscv64 (the whole "hardware")
    gdb-multiarch \           # GDB built with riscv64 support (Debian/Ubuntu naming)
    build-essential \         # linker/binutils/make for build scripts and C-adjacent tooling
    curl git
```

Version note: any QEMU ≥ 7.x is fine (Ubuntu 22.04+ ships qemu ≥ 6.2 which
also works). The exact version in use gets recorded in the M4 report for
reproducibility: `qemu-system-riscv64 --version`.

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

Expected: `TEST PASS: found "M0 BOOT OK"` and exit 0. `just test-panic`
exits 1; `just test-hang` exits 2.

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

## 6. Cloud agents

`.cursor/environment.json` runs `scripts/install.sh` to provision the same
toolchain in a fresh VM, so background agents can build and boot-test. Keep
that script in lockstep with this document.
