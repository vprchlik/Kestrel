/* Linker script for Kestrel on QEMU virt.
 *
 * Why 0x8020_0000: RAM on the virt machine starts at 0x8000_0000; OpenSBI
 * occupies (and PMP-protects) the first chunk of it and jumps to the next
 * boot stage at base + 2 MiB. QEMU loads a `-kernel` ELF at its linked
 * address, so linked address == loaded address == the address OpenSBI jumps
 * to. If these disagree, the machine hangs with zero output (DEBUGGING.md §4).
 *
 * Sections are 4 KiB-aligned so M1 can map each with distinct Sv39
 * permissions (text R+X, rodata R, data/bss R+W) at page granularity.
 * The __symbols are consumed by startup asm (stack, bss clearing in M0) and
 * by the M1 frame allocator / page-table setup.
 */

OUTPUT_ARCH(riscv)
ENTRY(_start)

BASE_ADDRESS = 0x80200000;

SECTIONS
{
    . = BASE_ADDRESS;
    __kernel_start = .;

    .text : {
        /* Entry stub must be the first byte of the image. */
        KEEP(*(.text.entry))
        *(.text .text.*)
    }

    . = ALIGN(4K);
    __rodata_start = .;
    .rodata : {
        *(.rodata .rodata.*)
        *(.srodata .srodata.*)
    }

    . = ALIGN(4K);
    __data_start = .;
    .data : {
        /* gp-relative addressing reaches ±2 KiB around __global_pointer$;
         * centering it in the small-data area lets the linker relax
         * absolute loads into cheaper gp-relative ones. */
        __global_pointer$ = . + 0x800;
        *(.sdata .sdata.*)
        *(.data .data.*)
    }

    . = ALIGN(4K);
    __bss_start = .;
    .bss : {
        *(.sbss .sbss.*)
        *(.bss .bss.*)
    }
    . = ALIGN(4K);
    __bss_end = .;

    /* D-0016: 4 KiB hole between .bss and the boot stack.
     *
     * Linker-script hole only — not a section, not in the image. Paging is
     * still off, so this range is ordinary RAM: a stack overflow still
     * stores into .bss exactly as when __boot_stack_bottom == __bss_end.
     * The guard is inert until T1.6 leaves this page out of the tables and
     * T1.7 activates translation. The frame allocator starts at __heap_end
     * and never hands this page out. */
    . += 4K;

    /* Boot stack: 64 KiB, NOLOAD. Kept outside __bss_start/__bss_end so the
     * M0 bss-clearing loop doesn't zero the stack it may be standing on. */
    .boot_stack (NOLOAD) : {
        __boot_stack_bottom = .;
        . += 64K;
        __boot_stack_top = .;
    }

    . = ALIGN(4K);
    __kernel_end = .;

    /* D-0024: 1 MiB heap, carved here before the free list. Location-counter
     * only — nothing is stored in the image.
     *   __heap_start / __heap_end : T1.6 maps the range R+W; T1.8's
     *     GlobalAlloc manages blocks inside it.
     *   __heap_end                : T1.4 free list starts here (and T1.6
     *     maps [__heap_end, RAM_END) as free frames). */
    __heap_start = .;
    . += 1M;
    __heap_end = .;

    /DISCARD/ : {
        *(.eh_frame .eh_frame_hdr)
    }
}
