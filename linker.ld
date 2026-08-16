/* Linker script for Whimbrel on QEMU virt.
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
 *
 * Four task slots are unrolled below (D-0030). src/task.rs `MAX_TASKS` must
 * match: check_layout asserts (__tasks_end - __tasks_start) / 88 KiB.
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
        /* D-0044 / D-0051: app and usys must not land here.
         * rustc passes `libapp-HASH.rlib`, whose members are
         * `app-HASH.*.rcgu.o`. Match both. If this stops working, stop
         * and inspect `cargo rustc -- --print link-args` rather than
         * widening the rule. Silent failure: app_main at 0x8020xxxx. */
        EXCLUDE_FILE(*libapp.rlib *libapp-*.rlib *libusys.rlib *libusys-*.rlib *app-*.rcgu.o *usys-*.rcgu.o)
            *(.text .text.*)
    }

    . = ALIGN(4K);
    __rodata_start = .;
    .rodata : {
        EXCLUDE_FILE(*libapp.rlib *libapp-*.rlib *libusys.rlib *libusys-*.rlib *app-*.rcgu.o *usys-*.rcgu.o)
            *(.rodata .rodata.*)
        EXCLUDE_FILE(*libapp.rlib *libapp-*.rlib *libusys.rlib *libusys-*.rlib *app-*.rcgu.o *usys-*.rcgu.o)
            *(.srodata .srodata.*)
    }

    . = ALIGN(4K);
    __data_start = .;
    .data : {
        /* gp-relative addressing reaches ±2 KiB around __global_pointer$;
         * centering it in the small-data area lets the linker relax
         * absolute loads into cheaper gp-relative ones. */
        __global_pointer$ = . + 0x800;
        EXCLUDE_FILE(*libapp.rlib *libapp-*.rlib *libusys.rlib *libusys-*.rlib *app-*.rcgu.o *usys-*.rcgu.o)
            *(.sdata .sdata.*)
        EXCLUDE_FILE(*libapp.rlib *libapp-*.rlib *libusys.rlib *libusys-*.rlib *app-*.rcgu.o *usys-*.rcgu.o)
            *(.data .data.*)
    }

    . = ALIGN(4K);
    __bss_start = .;
    .bss : {
        EXCLUDE_FILE(*libapp.rlib *libapp-*.rlib *libusys.rlib *libusys-*.rlib *app-*.rcgu.o *usys-*.rcgu.o)
            *(.sbss .sbss.*)
        EXCLUDE_FILE(*libapp.rlib *libapp-*.rlib *libusys.rlib *libusys-*.rlib *app-*.rcgu.o *usys-*.rcgu.o)
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

    /* T2.1 / D-0031 / D-0044: user sections. Assembly (`.utext`) and the
     * app/usys archives' ordinary sections land here. Each is 4 KiB-
     * aligned so it can have its own U-bit permissions. Content advances
     * `.` before `__tasks_start = .`, so the 88 KiB slots move up rather
     * than overlapping. */
    __utext_start = .;
    .utext : {
        *(.utext .utext.*)
        *libapp.rlib:(.text .text.*)
        *libapp-*.rlib:(.text .text.*)
        *app-*.rcgu.o:(.text .text.*)
        *libusys.rlib:(.text .text.*)
        *libusys-*.rlib:(.text .text.*)
        *usys-*.rcgu.o:(.text .text.*)
    }
    . = ALIGN(4K);
    __utext_end = .;

    __urodata_start = .;
    .urodata : {
        *(.urodata .urodata.*)
        /* LLVM names string literals `.rodata..Lanon.*`, not `.rodata`. */
        *libapp.rlib:(.rodata .rodata.* .srodata .srodata.*)
        *libapp-*.rlib:(.rodata .rodata.* .srodata .srodata.*)
        *app-*.rcgu.o:(.rodata .rodata.* .srodata .srodata.*)
        *libusys.rlib:(.rodata .rodata.* .srodata .srodata.*)
        *libusys-*.rlib:(.rodata .rodata.* .srodata .srodata.*)
        *usys-*.rcgu.o:(.rodata .rodata.* .srodata .srodata.*)
    }
    . = ALIGN(4K);
    __urodata_end = .;

    __udata_start = .;
    .udata : {
        *(.udata .udata.*)
        *libapp.rlib:(.data .data.* .sdata .sdata.*)
        *libapp-*.rlib:(.data .data.* .sdata .sdata.*)
        *app-*.rcgu.o:(.data .data.* .sdata .sdata.*)
        *libusys.rlib:(.data .data.* .sdata .sdata.*)
        *libusys-*.rlib:(.data .data.* .sdata .sdata.*)
        *usys-*.rcgu.o:(.data .data.* .sdata .sdata.*)
    }
    . = ALIGN(4K);
    __udata_end = .;

    __ubss_start = .;
    .ubss (NOLOAD) : {
        *(.ubss .ubss.*)
        *libapp.rlib:(.bss .bss.* .sbss .sbss.*)
        *libapp-*.rlib:(.bss .bss.* .sbss .sbss.*)
        *app-*.rcgu.o:(.bss .bss.* .sbss .sbss.*)
        *libusys.rlib:(.bss .bss.* .sbss .sbss.*)
        *libusys-*.rlib:(.bss .bss.* .sbss .sbss.*)
        *usys-*.rcgu.o:(.bss .bss.* .sbss .sbss.*)
    }
    . = ALIGN(4K);
    __ubss_end = .;

    /* Per-task stacks and break windows (D-0030). One slot:
     *   4 KiB ustack guard + 8 KiB ustack + 64 KiB break
     *   + 4 KiB kstack guard + 8 KiB kstack  = 88 KiB.
     *
     * Guard holes are linker-script holes only — not sections, not in the
     * image. Same situation as D-0016 in M1/T1.5: they are ordinary RAM
     * until T2.2 leaves them out of the tables. A stack overflow still
     * stores into the neighbouring region until then. The frame allocator
     * starts at __heap_end and never hands these pages out (they sit below
     * __kernel_end).
     *
     * `__ubrkN_wall == __kstackN_guard` is deliberate (D-0031): a `sbrk`
     * that passes its software bound check by one page lands in the kernel
     * stack's unmapped guard, not in the kernel stack. */
    __tasks_start = .;

    __ustack0_guard = .;  . += 4K;
    __ustack0_bottom = .; . += 8K;
    __ustack0_top = .;
    /* Shared boundary, not a missing guard: the user stack grows down
     * toward __ustackN_guard, the break grows up toward __ubrkN_wall.
     * They diverge from this address. Do not insert a hole here. */
    __ubrk0_base = .;     . += 64K;
    __ubrk0_wall = .;
    __kstack0_guard = .;  . += 4K;
    __kstack0_bottom = .; . += 8K;
    __kstack0_top = .;

    __ustack1_guard = .;  . += 4K;
    __ustack1_bottom = .; . += 8K;
    __ustack1_top = .;
    /* See task 0: shared ustack-top / break-base boundary. */
    __ubrk1_base = .;     . += 64K;
    __ubrk1_wall = .;
    __kstack1_guard = .;  . += 4K;
    __kstack1_bottom = .; . += 8K;
    __kstack1_top = .;

    __ustack2_guard = .;  . += 4K;
    __ustack2_bottom = .; . += 8K;
    __ustack2_top = .;
    /* See task 0: shared ustack-top / break-base boundary. */
    __ubrk2_base = .;     . += 64K;
    __ubrk2_wall = .;
    __kstack2_guard = .;  . += 4K;
    __kstack2_bottom = .; . += 8K;
    __kstack2_top = .;

    __ustack3_guard = .;  . += 4K;
    __ustack3_bottom = .; . += 8K;
    __ustack3_top = .;
    /* See task 0: shared ustack-top / break-base boundary. */
    __ubrk3_base = .;     . += 64K;
    __ubrk3_wall = .;
    __kstack3_guard = .;  . += 4K;
    __kstack3_bottom = .; . += 8K;
    __kstack3_top = .;

    __tasks_end = .;

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
