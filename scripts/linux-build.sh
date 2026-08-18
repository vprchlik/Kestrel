#!/usr/bin/env bash
# T4.8 / T4.8b / D-0062 / D-0073: Linux baseline build on the dedicated host.
#
# Inputs: bench/linux/{PIN,buildroot.fragment,linux-trimmed.fragment,
# server.c,initramfs.spec}. One buildroot tree plus one out-of-tree
# kernel dir. Prints five verification blocks; a build that cannot is
# a fail. Never runs inside a batch and never records a hash into PIN.
#
# D-0073 / T4.8b: a fragment change must rebuild Image-trimmed. Reuse
# is gated on a sha256 stamp of linux-trimmed.fragment, not merely on
# the Image existing. Image-stock must keep the T4.8 hash (do not
# rebuild stock; the version string is dated).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LINUX_DIR="$ROOT/bench/linux"
PIN="$LINUX_DIR/PIN"
FRAG_BR="$LINUX_DIR/buildroot.fragment"
FRAG_TRIM="$LINUX_DIR/linux-trimmed.fragment"
SERVER_C="$LINUX_DIR/server.c"
INITRAMFS_SPEC="$LINUX_DIR/initramfs.spec"
DL="$LINUX_DIR/dl"
BUILD="$LINUX_DIR/build"
ARTIFACTS="$LINUX_DIR/artifacts"
MANIFEST="$LINUX_DIR/MANIFEST"
NEED_FREE_BYTES=$((35 * 1000 * 1000 * 1000))
# T4.8 MANIFEST pins. Stock must not move. Trimmed must move after D-0073.
T48_STOCK_SHA=fa0f4315766866e7ce02e15f7bda78fdb73da69d4b9c8ae4f156b769a25eaf62
T48_TRIM_SHA=fe821d1d5fcc0c8d4474504c48d3024e0991c37ba74d40c675a0158b61e44fa2

die() {
    echo "TEST FAIL: $*" >&2
    exit 1
}

need_bin() {
    command -v "$1" >/dev/null 2>&1 || die "$1 not installed"
}

load_pin() {
    local line key val
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${line// }" ]] && continue
        key="${line%%=*}"
        val="${line#*=}"
        case "$key" in
            BUILDROOT_RELEASE|BUILDROOT_TARBALL|BUILDROOT_URL|BUILDROOT_SHA256|KERNEL_VERSION)
                printf -v "$key" '%s' "$val"
                ;;
            *) die "unknown PIN key $key" ;;
        esac
    done < "$PIN"
    [[ -n "${BUILDROOT_RELEASE:-}" ]] || die "PIN missing BUILDROOT_RELEASE"
    [[ -n "${BUILDROOT_TARBALL:-}" ]] || die "PIN missing BUILDROOT_TARBALL"
    [[ -n "${BUILDROOT_URL:-}" ]] || die "PIN missing BUILDROOT_URL"
    [[ -n "${BUILDROOT_SHA256:-}" && "$BUILDROOT_SHA256" != "UNSET" ]] \
        || die "PIN BUILDROOT_SHA256 is unset (fill the pin before building)"
    [[ -n "${KERNEL_VERSION:-}" ]] || die "PIN missing KERNEL_VERSION"
}

preflight() {
    local virt boost avail
    [[ -f "$PIN" ]] || die "missing $PIN"
    [[ -f "$FRAG_BR" ]] || die "missing $FRAG_BR"
    [[ -f "$FRAG_TRIM" ]] || die "missing $FRAG_TRIM"
    [[ -f "$SERVER_C" ]] || die "missing $SERVER_C"
    [[ -f "$INITRAMFS_SPEC" ]] || die "missing $INITRAMFS_SPEC"
    # systemd-detect-virt exits 1 on bare metal while still printing "none".
    virt="$(systemd-detect-virt 2>/dev/null || true)"
    [[ "$virt" == "none" ]] || die "bench host only (systemd-detect-virt=$virt, want none)"
    if [[ -f /sys/devices/system/cpu/cpufreq/boost ]]; then
        boost="$(cat /sys/devices/system/cpu/cpufreq/boost)"
        [[ "$boost" == "0" ]] || die "cpufreq boost is $boost (want 0; leave measurement discipline on)"
    else
        die "cpufreq boost sysfs missing"
    fi
    need_bin gcc
    need_bin make
    need_bin curl
    need_bin tar
    need_bin xz
    need_bin sha256sum
    need_bin python3
    need_bin bison
    need_bin flex
    need_bin bc
    mkdir -p "$DL" "$BUILD" "$ARTIFACTS"
    # Ubuntu 26.04's /usr/bin/install is uutils 0.8.0. Buildroot 2026.02.3
    # refuses it (uutils#12166). gnuinstall is already on this host from
    # GNU coreutils; the recipe prepends it rather than rewriting the
    # system alternative.
    if install --version 2>/dev/null | grep -q 'uutils'; then
        [[ -x /usr/bin/gnuinstall ]] || die "GNU install missing (/usr/bin/gnuinstall); package coreutils"
        mkdir -p "$BUILD/gnubin"
        ln -sfn /usr/bin/gnuinstall "$BUILD/gnubin/install"
        export PATH="$BUILD/gnubin:$PATH"
        install --version 2>/dev/null | grep -q 'GNU coreutils' \
            || die "PATH shim did not select GNU install"
        echo "linux-build: PATH prepends $BUILD/gnubin/install (GNU; uutils install is broken for this Buildroot)" >&2
    fi
    avail="$(df -B1 --output=avail "$LINUX_DIR" | tail -n1 | tr -d ' ')"
    if (( avail < NEED_FREE_BYTES )); then
        die "need ≥ 35 GB free on $(df -h "$LINUX_DIR" | tail -n1); have $avail bytes"
    fi
    curl -fsI --max-time 20 "$BUILDROOT_URL" >/dev/null \
        || die "network unreachable: HEAD $BUILDROOT_URL failed"
}

download_and_verify() {
    local tarpath="$DL/$BUILDROOT_TARBALL" got
    if [[ -f "$tarpath" ]]; then
        got="$(sha256sum "$tarpath" | awk '{print $1}')"
        if [[ "$got" != "$BUILDROOT_SHA256" ]]; then
            echo "linux-build: cached tarball hash mismatch, re-downloading" >&2
            rm -f "$tarpath"
        fi
    fi
    if [[ ! -f "$tarpath" ]]; then
        echo "linux-build: downloading $BUILDROOT_URL" >&2
        curl -L --fail --retry 3 -o "$tarpath.partial" "$BUILDROOT_URL"
        mv "$tarpath.partial" "$tarpath"
    fi
    got="$(sha256sum "$tarpath" | awk '{print $1}')"
    if [[ "$got" != "$BUILDROOT_SHA256" ]]; then
        die "tarball sha256=$got want $BUILDROOT_SHA256 (PIN). No trust-on-first-use."
    fi
    TARBALL_SHA_VERIFIED="$got"
}

extract_tree() {
    local dest="$BUILD/buildroot-$BUILDROOT_RELEASE"
    if [[ ! -d "$dest/.git" && ! -f "$dest/Makefile" ]]; then
        echo "linux-build: extracting $BUILDROOT_TARBALL" >&2
        rm -rf "$dest"
        tar -C "$BUILD" -xf "$DL/$BUILDROOT_TARBALL"
    fi
    BR_DIR="$dest"
    [[ -f "$BR_DIR/Makefile" ]] || die "extract produced no Makefile at $BR_DIR"
    DEFCONFIG="$BR_DIR/configs/qemu_riscv64_virt_defconfig"
    [[ -f "$DEFCONFIG" ]] || die "missing $DEFCONFIG"
    TREE_KERNEL="$(sed -n 's/^BR2_LINUX_KERNEL_CUSTOM_VERSION_VALUE="\(.*\)"$/\1/p' "$DEFCONFIG")"
    [[ -n "$TREE_KERNEL" ]] || die "qemu_riscv64_virt_defconfig does not pin a custom kernel version"
    if [[ "$TREE_KERNEL" != "$KERNEL_VERSION" ]]; then
        die "tree pins kernel $TREE_KERNEL, PIN says $KERNEL_VERSION"
    fi
}

apply_buildroot_fragment() {
    echo "linux-build: qemu_riscv64_virt_defconfig + buildroot.fragment" >&2
    (
        cd "$BR_DIR"
        make qemu_riscv64_virt_defconfig
        # Buildroot .config speaks BR2_*, same as the fragment. Concatenate
        # and olddefconfig so kconfig, not a hand merge, is the arbiter.
        cat "$FRAG_BR" >> .config
        make olddefconfig
    )
    grep -q '^BR2_TOOLCHAIN_BUILDROOT_MUSL=y' "$BR_DIR/.config" \
        || die "buildroot.fragment did not set BR2_TOOLCHAIN_BUILDROOT_MUSL=y"
    grep -q '^BR2_INIT_NONE=y' "$BR_DIR/.config" \
        || die "buildroot.fragment did not set BR2_INIT_NONE=y"
}

find_linux_src() {
    # linux-custom is the usual name for BR2_LINUX_KERNEL_CUSTOM_VERSION.
    if [[ -d "$BR_DIR/output/build/linux-custom" ]]; then
        LINUX_SRC="$BR_DIR/output/build/linux-custom"
    else
        LINUX_SRC="$(find "$BR_DIR/output/build" -maxdepth 1 -type d -name 'linux-[0-9]*' | head -n1)"
    fi
    [[ -n "$LINUX_SRC" && -f "$LINUX_SRC/Makefile" ]] \
        || die "cannot find pinned kernel source under $BR_DIR/output/build"
}

find_cross_compile() {
    HOST_DIR="$BR_DIR/output/host"
    local gcc_cands=()
    # Two names point at the same wrapper (riscv64-linux-gcc and
    # riscv64-buildroot-linux-musl-gcc). Prefer the musl SDK name.
    while IFS= read -r f; do
        gcc_cands+=("$f")
    done < <(find "$HOST_DIR/bin" -maxdepth 1 \( -type f -o -type l \) \
        -name '*-buildroot-linux-*-gcc' | sort)
    if (( ${#gcc_cands[@]} == 0 )); then
        while IFS= read -r f; do
            gcc_cands+=("$f")
        done < <(find "$HOST_DIR/bin" -maxdepth 1 \( -type f -o -type l \) \
            -name '*-gcc' | sort)
    fi
    if (( ${#gcc_cands[@]} != 1 )); then
        die "expected one SDK *-gcc under $HOST_DIR/bin, found ${#gcc_cands[@]}: ${gcc_cands[*]:-none}"
    fi
    CROSS_COMPILE="${gcc_cands[0]%gcc}"
}

build_stock_linux() {
    local jobs="${BR2_JLEVEL:-$(nproc)}"
    find_linux_src
    # Resume path: mrproper wipes the in-tree stock build. Reuse the
    # already-copied Image and .config rather than rebuilding 6.18.7.
    if [[ -f "$ARTIFACTS/Image-stock" && -f "$BUILD/stock.config" ]]; then
        find_cross_compile
        STOCK_IMAGE="$ARTIFACTS/Image-stock"
        STOCK_KCONFIG="$BUILD/stock.config"
        STOCK_LABEL="stock"
        echo "linux-build: reusing $STOCK_IMAGE (skip make linux)" >&2
        return
    fi
    echo "linux-build: make linux -j$jobs (stock kernel + toolchain)" >&2
    (
        cd "$BR_DIR"
        make linux "BR2_JLEVEL=$jobs"
    )
    STOCK_IMAGE="$BR_DIR/output/images/Image"
    [[ -f "$STOCK_IMAGE" ]] || die "stock build produced no $STOCK_IMAGE"
    find_linux_src
    STOCK_KCONFIG="$LINUX_SRC/.config"
    [[ -f "$STOCK_KCONFIG" ]] || die "stock kernel .config missing at $STOCK_KCONFIG"
    find_cross_compile
    # One-line virtio-net built-in if the board config ships =m (D-0062).
    STOCK_LABEL="stock"
    local virtio
    virtio="$(grep -E '^CONFIG_VIRTIO_NET=' "$STOCK_KCONFIG" || true)"
    if [[ "$virtio" == "CONFIG_VIRTIO_NET=m" ]]; then
        echo "linux-build: stock CONFIG_VIRTIO_NET=m; applying built-in fragment" >&2
        STOCK_LABEL="stock + virtio built-in"
        printf 'CONFIG_VIRTIO_NET=y\n' > "$BUILD/virtio-builtin.fragment"
        (
            cd "$LINUX_SRC"
            ARCH=riscv CROSS_COMPILE="$CROSS_COMPILE" \
                ./scripts/kconfig/merge_config.sh \
                -O "$LINUX_SRC" "$STOCK_KCONFIG" "$BUILD/virtio-builtin.fragment"
        )
        (
            cd "$BR_DIR"
            make linux-rebuild "BR2_JLEVEL=$jobs"
        )
        grep -q '^CONFIG_VIRTIO_NET=y' "$STOCK_KCONFIG" \
            || die "virtio-net built-in fragment did not stick"
        [[ -f "$STOCK_IMAGE" ]] || die "stock rebuild produced no Image"
    fi
    cp -a "$STOCK_IMAGE" "$ARTIFACTS/Image-stock"
    cp -a "$STOCK_KCONFIG" "$BUILD/stock.config"
}

build_trimmed_linux() {
    local jobs="${BR2_JLEVEL:-$(nproc)}"
    local frag_sha stamp
    TRIM_O="$BUILD/linux-trimmed"
    frag_sha="$(sha256sum "$FRAG_TRIM" | awk '{print $1}')"
    stamp="$BUILD/trimmed.fragment.sha256"
    if [[ "${FORCE_TRIMMED_REBUILD:-}" != "1" \
        && -f "$ARTIFACTS/Image-trimmed" \
        && -f "$BUILD/trimmed.config" \
        && -f "$BUILD/merge_config.out" \
        && -f "$stamp" \
        && "$(cat "$stamp")" == "$frag_sha" ]]; then
        TRIM_IMAGE="$ARTIFACTS/Image-trimmed"
        echo "linux-build: reusing $TRIM_IMAGE (fragment sha unchanged)" >&2
        # D-0072: System.map is the offline initcall labeler. Copy it
        # if the O= tree still has it and the artifact was never saved.
        if [[ ! -f "$ARTIFACTS/System.map-trimmed" ]]; then
            if [[ -f "$TRIM_O/System.map" ]]; then
                cp -a "$TRIM_O/System.map" "$ARTIFACTS/System.map-trimmed"
            else
                echo "linux-build: WARNING: System.map-trimmed missing (D-0072 diagnostic needs it; do not rebuild Image-trimmed to recover)" >&2
            fi
        fi
        return
    fi
    # Buildroot builds the pinned kernel in-tree under output/build/linux-*.
    # The kernel Makefile refuses O= against that dirty tree ("please run
    # 'make mrproper'"). Stock Image and .config are already copied out.
    echo "linux-build: make ARCH=riscv mrproper in $LINUX_SRC (required for O= trimmed)" >&2
    make -C "$LINUX_SRC" ARCH=riscv mrproper
    rm -rf "$TRIM_O"
    mkdir -p "$TRIM_O"
    echo "linux-build: merge linux-trimmed.fragment (out-of-tree O=$TRIM_O)" >&2
    (
        cd "$LINUX_SRC"
        ARCH=riscv CROSS_COMPILE="$CROSS_COMPILE" \
            ./scripts/kconfig/merge_config.sh \
            -O "$TRIM_O" "$BUILD/stock.config" "$FRAG_TRIM"
    ) > "$BUILD/merge_config.out" 2>&1 || {
        cat "$BUILD/merge_config.out" >&2
        die "merge_config.sh failed for trimmed kernel"
    }
    # Gate block 3 before compiling: a dependency re-enable is a fragment
    # annotation, not a 20-minute surprise after Image. Copy .config
    # first so the keeps check sees the final file.
    cp -a "$TRIM_O/.config" "$BUILD/trimmed.config"
    check_merge_warnings "$FRAG_TRIM" "$BUILD/merge_config.out"
    assert_keeps > "$BUILD/keeps.out" || {
        cat "$BUILD/keeps.out" >&2
        die "D-0062 keep missing from trimmed.config"
    }
    requested_vs_final > "$BUILD/requested_vs_final.out" || {
        cat "$BUILD/requested_vs_final.out" >&2
        die "requested-vs-final failed after merge (annotate the fragment, do not skip)"
    }
    echo "linux-build: make Image (trimmed) -j$jobs" >&2
    make -C "$LINUX_SRC" O="$TRIM_O" ARCH=riscv CROSS_COMPILE="$CROSS_COMPILE" \
        Image -j"$jobs"
    TRIM_IMAGE="$TRIM_O/arch/riscv/boot/Image"
    [[ -f "$TRIM_IMAGE" ]] || die "trimmed build produced no $TRIM_IMAGE"
    [[ -f "$TRIM_O/System.map" ]] || die "trimmed build produced no System.map"
    cp -a "$TRIM_IMAGE" "$ARTIFACTS/Image-trimmed"
    cp -a "$TRIM_O/System.map" "$ARTIFACTS/System.map-trimmed"
    cp -a "$TRIM_O/.config" "$BUILD/trimmed.config"
    echo "$frag_sha" > "$BUILD/trimmed.fragment.sha256"
}

check_merge_warnings() {
    # Three "not in final .config" cases (scripts/linux-merge-warnings.py):
    # 1. fragment unset → final y: survival, merge-override or abort
    # 2. fragment unset → final absent: menu vanished, success
    # 3. stock =y, not in fragment, final absent: dependent drop, success
    # Redefined/redundant stay informational. Keeps are a separate check.
    local frag="$1" log="$2"
    python3 "$ROOT/scripts/linux-merge-warnings.py" "$frag" "$log" \
        || die "merge_config.sh reported an unannotated value that did not stick"
}

assert_keeps() {
    python3 "$ROOT/scripts/linux-merge-warnings.py" keeps "$BUILD/trimmed.config"
}

requested_vs_final() {
    python3 - "$FRAG_TRIM" "$BUILD/trimmed.config" <<'PY'
import re
import sys
from pathlib import Path

frag_path, cfg_path = Path(sys.argv[1]), Path(sys.argv[2])
frag = frag_path.read_text()
cfg = cfg_path.read_text()

final = {}
for line in cfg.splitlines():
    m = re.match(r"CONFIG_([A-Z0-9_]+)=(.*)$", line)
    if m:
        final[m.group(1)] = m.group(2).strip()
        continue
    m = re.match(r"# CONFIG_([A-Z0-9_]+) is not set", line)
    if m:
        final[m.group(1)] = "unset"

def annotated(sym: str) -> bool:
    # Intent notes ("PROC_FS: aggressive") do not count. A dependency
    # re-enable is only accepted with an explicit merge-override line.
    pat = re.compile(rf"# merge-override (?:CONFIG_)?{re.escape(sym)}\b")
    return any(pat.match(raw.strip()) for raw in frag.splitlines())

req_y = re.compile(r"^CONFIG_([A-Z0-9_]+)=y\b")
req_n = re.compile(r"^# CONFIG_([A-Z0-9_]+) is not set")
print("===== 3. requested vs final =====")
failed = []
for raw in frag.splitlines():
    s = raw.strip()
    m = req_y.match(s)
    if m:
        sym, want = m.group(1), "y"
    else:
        m = req_n.match(s)
        if not m:
            continue
        sym, want = m.group(1), "unset"
    got = final.get(sym, "unset")
    if want == "y":
        ok = got == "y"
        want_s, got_s = "y", got
    else:
        ok = got == "unset"
        want_s, got_s = "unset", got
    status = "PASS" if ok else "FAIL"
    print(f"CONFIG_{sym}: requested {want_s}, final {got_s}  {status}")
    if not ok:
        failed.append((sym, want_s, got_s))

for sym, want_s, got_s in failed:
    if annotated(sym):
        print(f"annotated override: CONFIG_{sym} requested {want_s}, final {got_s}")
        continue
    sys.stderr.write(
        f"TEST FAIL: merge override not annotated: CONFIG_{sym} requested "
        f"{want_s}, final {got_s}\n"
    )
    sys.exit(1)
PY
}

assert_d0073_unsets() {
    python3 - "$BUILD/trimmed.config" <<'PY'
import re
import sys
from pathlib import Path

cfg = Path(sys.argv[1]).read_text()
final = {}
for line in cfg.splitlines():
    m = re.match(r"CONFIG_([A-Z0-9_]+)=(.*)$", line)
    if m:
        final[m.group(1)] = m.group(2).strip()
        continue
    m = re.match(r"# CONFIG_([A-Z0-9_]+) is not set", line)
    if m:
        final[m.group(1)] = "unset"

# D-0073: these must not be y. Absent/unset is a pass (menu vanished).
must_not_be_y = (
    "FTRACE",
    "NETWORK_FILESYSTEMS",
    "NFS_FS",
    "NET_9P",
    "9P_FS",
    "USB_SUPPORT",
    "USB",
    "SOUND",
    "SND",
    "MMC",
    "INPUT_MOUSEDEV",
    "INPUT_MOUSE",
    "HID",
    "HUGETLBFS",
    "AUDIT",
    "BPF_SYSCALL",
    "ACPI",
    "PNP",
    "LEGACY_PTYS",
    "UNIX98_PTYS",
    "RTC_CLASS",
    "RTC_DRV_GOLDFISH",
    "WATCHDOG",
)
print("===== 3b. D-0073 leftovers must not be y =====")
failed = []
for sym in must_not_be_y:
    got = final.get(sym, "absent")
    ok = got != "y"
    status = "PASS" if ok else "FAIL"
    print(f"CONFIG_{sym}: final {got}  {status}")
    if not ok:
        failed.append(sym)
if failed:
    sys.stderr.write(
        "TEST FAIL: D-0073 leftover still y: "
        + ", ".join(f"CONFIG_{s}" for s in failed)
        + "\n"
    )
    sys.exit(1)
PY
}

build_init_and_cpio() {
    local gcc="${CROSS_COMPILE}gcc" strip="${CROSS_COMPILE}strip" spec tmp
    echo "linux-build: static musl /init" >&2
    "$gcc" -static -Os -std=c11 -Wall -Werror -o "$ARTIFACTS/init" "$SERVER_C"
    "$strip" "$ARTIFACTS/init"
    echo "linux-build: gen_init_cpio → rootfs.cpio" >&2
    gcc -O2 -Wall -o "$BUILD/gen_init_cpio" "$LINUX_SRC/usr/gen_init_cpio.c"
    tmp="$(mktemp)"
    grep -vE '^[[:space:]]*(#|$)' "$INITRAMFS_SPEC" > "$tmp"
    if grep -q 'getopt' "$LINUX_SRC/usr/gen_init_cpio.c" \
        && grep -q 'mtime' "$LINUX_SRC/usr/gen_init_cpio.c"; then
        "$BUILD/gen_init_cpio" -t 0 "$tmp" > "$ARTIFACTS/rootfs.cpio"
    else
        die "usr/gen_init_cpio has no -t mtime pin; cannot keep the cpio deterministic"
    fi
    rm -f "$tmp"
    [[ -s "$ARTIFACTS/rootfs.cpio" ]] || die "rootfs.cpio empty"
}

sha256_of() { sha256sum "$1" | awk '{print $1}'; }

write_manifest_and_blocks() {
    local stock_h trim_h cpio_h init_h
    local stock_b trim_b cpio_b init_b
    stock_h="$(sha256_of "$ARTIFACTS/Image-stock")"
    trim_h="$(sha256_of "$ARTIFACTS/Image-trimmed")"
    cpio_h="$(sha256_of "$ARTIFACTS/rootfs.cpio")"
    init_h="$(sha256_of "$ARTIFACTS/init")"
    stock_b="$(stat -c%s "$ARTIFACTS/Image-stock")"
    trim_b="$(stat -c%s "$ARTIFACTS/Image-trimmed")"
    cpio_b="$(stat -c%s "$ARTIFACTS/rootfs.cpio")"
    init_b="$(stat -c%s "$ARTIFACTS/init")"
    local quiet='console=ttyS0 quiet loglevel=0 rdinit=/init'
    local instr='console=ttyS0 loglevel=7 printk.time=1 initcall_debug rdinit=/init'
    cat > "$MANIFEST" <<EOF
# generated by just linux-build — do not edit
# stock_label=$STOCK_LABEL kernel_version=$TREE_KERNEL buildroot_release=$BUILDROOT_RELEASE
artifact Image-stock $stock_h
artifact Image-trimmed $trim_h
artifact rootfs.cpio $cpio_h
artifact init $init_h
append quiet $quiet
append instrumented $instr
EOF

    echo "===== 1. pin echo ====="
    echo "buildroot_release=$BUILDROOT_RELEASE"
    echo "tarball_sha256=$TARBALL_SHA_VERIFIED verified OK"
    echo "kernel_version=$TREE_KERNEL (qemu_riscv64_virt_defconfig)"
    echo "stock_kernel_config=$STOCK_LABEL"

    echo "===== 2. merge_config.sh (trimmed, unfiltered) ====="
    cat "$BUILD/merge_config.out"

    requested_vs_final

    assert_d0073_unsets

    assert_keeps

    echo "===== 4. diffconfig stock → trimmed ====="
    if [[ -x "$LINUX_SRC/scripts/diffconfig" ]]; then
        "$LINUX_SRC/scripts/diffconfig" "$BUILD/stock.config" "$BUILD/trimmed.config"
    else
        python3 "$LINUX_SRC/scripts/diffconfig" "$BUILD/stock.config" "$BUILD/trimmed.config"
    fi

    echo "===== 5. artifacts + MANIFEST ====="
    printf '%-42s %12s  %s\n' path bytes sha256
    printf '%-42s %12s  %s\n' bench/linux/artifacts/Image-stock "$stock_b" "$stock_h"
    printf '%-42s %12s  %s\n' bench/linux/artifacts/Image-trimmed "$trim_b" "$trim_h"
    printf '%-42s %12s  %s\n' bench/linux/artifacts/rootfs.cpio "$cpio_b" "$cpio_h"
    printf '%-42s %12s  %s\n' bench/linux/artifacts/init "$init_b" "$init_h"
    echo "----- MANIFEST -----"
    cat "$MANIFEST"
    if [[ "$stock_h" != "$T48_STOCK_SHA" ]]; then
        die "Image-stock sha256=$stock_h want $T48_STOCK_SHA (T4.8 pin; stock must not move)"
    fi
    if [[ "$trim_h" == "$T48_TRIM_SHA" ]]; then
        die "Image-trimmed sha256 still $T48_TRIM_SHA (fragment change did not produce a new Image)"
    fi
    echo "TEST PASS: linux-build"
}

main() {
    python3 "$ROOT/scripts/linux-merge-warnings.py" selftest \
        || die "linux-merge-warnings selftest failed"
    load_pin
    preflight
    download_and_verify
    extract_tree
    echo "===== 1. pin echo (early) =====" >&2
    echo "buildroot_release=$BUILDROOT_RELEASE tarball verified OK kernel=$TREE_KERNEL" >&2
    apply_buildroot_fragment
    build_stock_linux
    build_trimmed_linux
    build_init_and_cpio
    write_manifest_and_blocks
}

main "$@"
