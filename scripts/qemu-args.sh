# Single QEMU argv definition (D-0055 / audit finding 28).
# Sourced by boot-test.sh and bench.py; executed to print the justfile string.
# A new flag lands here, not in a fifth copy.
#
# Usage (source): qemu_args_fill [pcap] [tcp_hostfwd_port]
# Usage (exec):   bash scripts/qemu-args.sh [pcap] [tcp_hostfwd_port]

qemu_args_fill() {
    local pcap="${1:-whimbrel.pcap}"
    local tcp_port="${2:-8080}"
    QEMU="${QEMU:-qemu-system-riscv64}"
    QEMU_ARGS=(
        -machine virt
        -nographic
        -bios "${QEMU_BIOS:-default}"
        -global virtio-mmio.force-legacy=false
        -netdev "user,id=net0,hostfwd=tcp::${tcp_port}-:80,hostfwd=udp::7777-:7"
        -device virtio-net-device,netdev=net0
        -object "filter-dump,id=f0,netdev=net0,file=${pcap}"
    )
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set -euo pipefail
    qemu_args_fill "$@"
    printf '%s' "${QEMU_ARGS[0]}"
    local_i=1
    while [ "$local_i" -lt "${#QEMU_ARGS[@]}" ]; do
        printf ' %s' "${QEMU_ARGS[$local_i]}"
        local_i=$((local_i + 1))
    done
    printf '\n'
fi
