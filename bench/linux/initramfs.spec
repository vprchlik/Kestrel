# bench/linux/initramfs.spec — deterministic cpio (gen_init_cpio).
# Paths on the right are repo-relative; just linux-build runs from
# the repo root. Fixed mode/uid/gid keep the archive reproducible.
dir /dev 0755 0 0
nod /dev/console 0600 0 0 c 5 1
file /init bench/linux/artifacts/init 0755 0 0
