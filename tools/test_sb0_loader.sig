//! Public, self-contained SB0 ABI conformance fixture for QEMU virt.
//! This is a single-process test kernel, not an SB0S operating-system image.
//! It loads the real SB0X bytes, maps RX/RW/BSS, prepares the 256-byte BHB and
//! delegated handle, enters EL0, and implements only the stream test's traps.
const source_base: usize = 0x48000000;
const image_base: usize = 0x00400000;
const physical_bias: usize = 0x40000000;
const l1_base: usize = 0x40100000;
const l2_base: usize = 0x40101000;
const l3_base: usize = 0x40102000;
const stack_top: usize = 0x07800000;
const handoff_base: usize = 0x07c00000;
const vector_base: usize = 0x40190000;
var saw_svc: u64 = 0;

fn pointer(address: usize) [*]volatile u8 {
    return @ptrFromInt(address);
}
fn table(address: usize) [*]volatile u64 {
    return @ptrFromInt(address);
}
fn read16(offset: usize) usize {
    const p = pointer(source_base + offset);
    return @as(usize, p[0]) | (@as(usize, p[1]) << 8);
}
fn read32(offset: usize) usize {
    const p = pointer(source_base + offset);
    var value: usize = 0;
    var i: usize = 0;
    while (i < 4) : (i += 1) value |= @as(usize, p[i]) << @intCast(i * 8);
    return value;
}
fn read64(offset: usize) usize {
    const p = pointer(source_base + offset);
    var value: usize = 0;
    var i: usize = 0;
    while (i < 8) : (i += 1) value |= @as(usize, p[i]) << @intCast(i * 8);
    return value;
}
fn uart() *volatile u32 {
    return @ptrFromInt(0x09000000);
}
fn flags() u32 {
    return @as(*volatile u32, @ptrFromInt(0x09000018)).*;
}
fn byte(value: u8) void {
    while (flags() & 32 != 0) {}
    uart().* = value;
}
fn print(text: []const u8) void {
    for (text) |value| byte(value);
}
fn hexadecimal(value: u64) void {
    var n: usize = 16;
    while (n > 0) {
        n -= 1;
        const digit: u8 = @truncate((value >> @intCast(n * 4)) & 15);
        byte(if (digit < 10) '0' + digit else 'a' + digit - 10);
    }
}
fn park() noreturn {
    while (true) asm volatile ("wfe");
}
fn reject() noreturn {
    print("SB0-ABI-LOADER-REJECT\n");
    park();
}
fn zero(address: usize, length: usize) void {
    const p = pointer(address);
    var i: usize = 0;
    while (i < length) : (i += 1) p[i] = 0;
}
fn write32(address: usize, value: u32) void {
    const p = pointer(address);
    var i: usize = 0;
    while (i < 4) : (i += 1) p[i] = @truncate(value >> @intCast(i * 8));
}
fn write64(address: usize, value: usize) void {
    table(address)[0] = value;
}

fn mapPage(virtual: usize, writable: bool, executable: bool) void {
    const block = virtual >> 21;
    if (block >= 128 or virtual & 4095 != 0) reject();
    const l3 = l3_base + block * 4096;
    if (table(l2_base)[block] == 0) {
        zero(l3, 4096);
        table(l2_base)[block] = l3 | 3;
    }
    const index = (virtual >> 12) & 511;
    if (table(l3)[index] != 0) reject();
    // AF + inner-shareable Normal memory; AP grants EL0 RO or RW. PXN
    // prevents executing userspace through EL1; UXN protects data and stack.
    var attributes: u64 = 0x707 | (@as(u64, 1) << 53);
    attributes |= if (writable) @as(u64, 0x40) else @as(u64, 0xc0);
    if (!executable) attributes |= @as(u64, 1) << 54;
    table(l3)[index] = (virtual + physical_bias) | attributes;
}

fn userBuffer(address: usize, length: usize, writable: bool) bool {
    if (length == 0) return true;
    if (address < image_base or address >= 0x10000000 or length > 0x1000000 or length > 0x10000000 - address) return false;
    var current = address & ~@as(usize, 4095);
    const end = address + length;
    while (current < end) : (current += 4096) {
        const block = current >> 21;
        if (table(l2_base)[block] & 3 != 3) return false;
        const descriptor = table(l3_base + block * 4096)[(current >> 12) & 511];
        if (descriptor & 3 != 3 or descriptor & 0x40 == 0 or (writable and descriptor & 0x80 != 0)) return false;
    }
    return true;
}

fn initializeVectors() void {
    // Fixed aligned vector slots avoid relying on compiler function placement.
    // Each standard AArch64 stub preserves x16/x17, loads the absolute handler
    // address, and branches. The synchronous handler restores them before its
    // complete register save. Kernel RAM is inaccessible to EL0.
    zero(vector_base, 2048);
    var index: usize = 0;
    while (index < 16) : (index += 1) {
        const at = vector_base + index * 128;
        write32(at, 0xa9bf47f0); // stp x16,x17,[sp,#-16]!
        write32(at + 4, 0x58000070); // ldr x16, literal at +16
        write32(at + 8, 0xd61f0200); // br x16
        write32(at + 12, 0xd503201f); // nop (align 64-bit literal)
        write64(at + 16, if (index == 8) @intFromPtr(&loaderSync) else @intFromPtr(&loaderFaultAsm));
    }
}

export fn loaderFault(esr: u64, far: u64, elr: u64, user_sp: u64, user_fp: u64, user_lr: u64) callconv(.c) noreturn {
    print("SB0-ABI-LOADER-FAULT esr=");
    hexadecimal(esr);
    print(" far=");
    hexadecimal(far);
    print(" elr=");
    hexadecimal(elr);
    print(" sp=");
    hexadecimal(user_sp);
    print(" fp=");
    hexadecimal(user_fp);
    print(" lr=");
    hexadecimal(user_lr);
    print("\n");
    park();
}

export fn loaderTrap(frame: [*]u64, esr: u64) callconv(.c) void {
    if (esr >> 26 != 0x15 or esr & 65535 != 0 or frame[33] & 15 != 0) loaderFault(esr, 0, frame[32], frame[31], frame[29], frame[30]);
    if (saw_svc == 0) {
        saw_svc = 1;
        print("SB0-ABI-LOADER-SVC-EL0\n");
    }
    const opcode = frame[8];
    const a = frame[0];
    const b = frame[1];
    const c = frame[2];
    frame[0] = 0;
    frame[1] = 0;
    if (opcode == 0) {
        print(if (a == 0) "SB0-ABI-LOADER-EXIT code=0\n" else "SB0-ABI-LOADER-EXIT code=nonzero\n");
        park();
    } else if (opcode == 0x0f00) {
        if (!userBuffer(a, b, false)) {
            frame[1] = 4;
            return;
        }
        const p = pointer(a);
        var i: usize = 0;
        while (i < b) : (i += 1) byte(p[i]);
        frame[0] = b;
    } else if (opcode == 0x0403) {
        return;
    } else if (opcode == 0x0601 or opcode == 0x0602) {
        if (a != 1) {
            frame[1] = 1;
            return;
        }
        if (!userBuffer(b, c, opcode == 0x0602)) {
            frame[1] = 4;
            return;
        }
        const p = pointer(b);
        var count: usize = 0;
        if (opcode == 0x0601) {
            while (count < c) : (count += 1) byte(p[count]);
        } else {
            while (count < c and flags() & 16 == 0) : (count += 1) p[count] = @truncate(uart().*);
            if (count == 0 and c != 0) frame[1] = 11;
        }
        frame[0] = count;
    } else frame[1] = 13;
}

extern fn loaderEnter(u64, u64, u64, u64) callconv(.c) noreturn;

export fn loaderMain() callconv(.c) noreturn {
    saw_svc = 0;
    initializeVectors();
    print("SB0-ABI-LOADER-START public single-process conformance fixture\n");
    const length: usize = table(0x47e00000)[0];
    if (length < 64 or length > 0x1000000 or read32(0) != 0x58304253 or pointer(source_base)[4] != 1 or pointer(source_base)[5] != 0 or read16(6) != 1) reject();
    const segments = read16(16);
    const image_size = read64(32);
    const stack_size = read64(40);
    const entry = read64(8);
    if (segments == 0 or segments > 8 or 64 + segments * 40 > length or image_size == 0 or image_size > 0x4000000 or stack_size < 4096 or stack_size > 0x1000000 or stack_size & 4095 != 0) reject();
    const stack_base = stack_top - stack_size;
    if (read16(18) != 0 or read32(20) != 0 or read32(24) != 0 or read32(28) != 0 or read64(48) != 0 or read64(56) != 0) reject();
    var executable_entry = false;
    var index: usize = 0;
    while (index < segments) : (index += 1) {
        const at = 64 + index * 40;
        const file_at = read64(at);
        const virtual = read64(at + 8);
        const file_size = read64(at + 16);
        const mem_size = read64(at + 24);
        const permissions = read32(at + 32);
        if (read32(at + 36) != 0 or permissions & 1 == 0 or permissions & ~@as(usize, 7) != 0 or permissions & 6 == 6 or virtual & 4095 != 0 or mem_size == 0 or virtual > image_size or mem_size > image_size - virtual or file_size > mem_size or file_at > length or file_size > length - file_at or (file_size != 0 and file_at < 64 + segments * 40)) reject();
        if (permissions & 4 != 0 and entry >= virtual and entry - virtual < file_size) executable_entry = true;
        var other: usize = 0;
        while (other < index) : (other += 1) {
            const previous = 64 + other * 40;
            const begin = read64(previous + 8);
            const end = begin + read64(previous + 24);
            if (virtual < end and begin < virtual + mem_size) reject();
        }
    }
    if (!executable_entry) reject();
    zero(l1_base, 8192);
    table(l1_base)[0] = l2_base | 3;
    table(l1_base)[1] = 0x40000705; // Kernel physical RAM, EL1 only.
    table(l2_base)[72] = 0x09000401 | (@as(u64, 3) << 53); // Privileged PL011.
    index = 0;
    while (index < segments) : (index += 1) {
        const at = 64 + index * 40;
        const virtual = read64(at + 8);
        const file_at = read64(at);
        const file_size = read64(at + 16);
        const mem_size = read64(at + 24);
        const permissions = read32(at + 32);
        zero(physical_bias + image_base + virtual, mem_size);
        const destination = pointer(physical_bias + image_base + virtual);
        const input = pointer(source_base + file_at);
        var n: usize = 0;
        while (n < file_size) : (n += 1) destination[n] = input[n];
        n = 0;
        while (n < mem_size) : (n += 4096) mapPage(image_base + virtual + n, permissions & 2 != 0, permissions & 4 != 0);
    }
    zero(physical_bias + stack_base, stack_size);
    var n: usize = 0;
    while (n < stack_size) : (n += 4096) mapPage(stack_base + n, true, false);
    zero(physical_bias + handoff_base, 8192);
    write32(physical_bias + handoff_base, 0x53423048);
    write32(physical_bias + handoff_base + 8, 1);
    write64(physical_bias + handoff_base + 16, stack_base);
    write64(physical_bias + handoff_base + 24, stack_size);
    write64(physical_bias + handoff_base + 48, image_base);
    write64(physical_bias + handoff_base + 56, image_size);
    write32(physical_bias + handoff_base + 80, 1);
    write32(physical_bias + handoff_base + 84, 256);
    // Handle 1 is a delegated read/write channel. Table and BHB are read-only.
    pointer(physical_bias + handoff_base + 4096 + 12)[0] = 6;
    pointer(physical_bias + handoff_base + 4096 + 14)[0] = 3;
    write32(physical_bias + handoff_base + 4096 + 16, 1);
    write32(physical_bias + handoff_base + 4096 + 20, 1);
    write32(physical_bias + handoff_base + 4096 + 3072, 2);
    write32(physical_bias + handoff_base + 4096 + 3076, 1);
    mapPage(handoff_base, false, false);
    mapPage(handoff_base + 4096, false, false);
    asm volatile (
        \\ movz x9, #0x4010, lsl #16
        \\ msr ttbr0_el1, x9
        \\ movz x9, #0xff00
        \\ msr mair_el1, x9
        \\ movz x9, #0x3519
        \\ movk x9, #0x0080, lsl #16
        \\ movk x9, #0x0002, lsl #32
        \\ msr tcr_el1, x9
        \\ dsb sy
        // Cold boot has never enabled translation: there are no old mappings.
        \\ isb
        \\ mrs x9, sctlr_el1
        \\ movz x10, #0xffe5
        \\ movk x10, #0xffff, lsl #16
        \\ movk x10, #0xffff, lsl #32
        \\ movk x10, #0xffff, lsl #48
        \\ and x9, x9, x10
        \\ mov x10, #0x1005
        \\ orr x9, x9, x10
        \\ msr sctlr_el1, x9
        \\ isb
        \\ ic iallu
        \\ dsb sy
        \\ isb
        ::: .{ .memory = true, .x9 = true, .x10 = true });
    print("SB0-ABI-LOADER-MAPPED RX RW BSS stack BHB handles; entering EL0\n");
    loaderEnter(image_base + entry, stack_base + stack_size, handoff_base, handoff_base + 4096);
}

export fn _start() callconv(.naked) noreturn {
    asm volatile (
        \\ movz x9, #0x403f, lsl #16
        \\ mrs x10, CurrentEL
        \\ cmp x10, #8
        \\ b.ne 1f
        \\ msr S3_4_C4_C1_0, x9
        \\ mov x10, #0x80000000
        \\ msr hcr_el2, x10
        \\ mov x10, #0x3c5
        \\ msr S3_4_C4_C0_0, x10
        \\ adr x10, 1f
        \\ msr S3_4_C4_C0_1, x10
        \\ eret
        \\1: mov sp, x9
        \\ movz x10, #0x30, lsl #16
        \\ msr cpacr_el1, x10
        \\ movz x10, #0x4019, lsl #16
        \\ msr vbar_el1, x10
        \\ isb
        \\ bl %[main]
        \\ b .
        :
        : [main] "S" (&loaderMain),
        : .{ .memory = true });
}

export fn loaderFaultAsm() callconv(.naked) noreturn {
    asm volatile (
        \\ mrs x0, esr_el1
        \\ mrs x1, far_el1
        \\ mrs x2, S3_0_C4_C0_1
        \\ mrs x3, S3_0_C4_C1_0
        \\ mov x4, x29
        \\ mov x5, x30
        \\ b %[fault]
        :
        : [fault] "S" (&loaderFault),
        : .{ .memory = true });
}

export fn loaderSync() callconv(.naked) noreturn {
    asm volatile (
        \\ ldp x16, x17, [sp], #16
        \\ sub sp, sp, #784
        \\ stp x0, x1, [sp, #0]
        \\ stp x2, x3, [sp, #16]
        \\ stp x4, x5, [sp, #32]
        \\ stp x6, x7, [sp, #48]
        \\ stp x8, x9, [sp, #64]
        \\ stp x10, x11, [sp, #80]
        \\ stp x12, x13, [sp, #96]
        \\ stp x14, x15, [sp, #112]
        \\ stp x16, x17, [sp, #128]
        \\ stp x18, x19, [sp, #144]
        \\ stp x20, x21, [sp, #160]
        \\ stp x22, x23, [sp, #176]
        \\ stp x24, x25, [sp, #192]
        \\ stp x26, x27, [sp, #208]
        \\ stp x28, x29, [sp, #224]
        \\ str x30, [sp, #240]
        \\ mrs x0, S3_0_C4_C1_0
        \\ str x0, [sp, #248]
        \\ mrs x0, S3_0_C4_C0_1
        \\ str x0, [sp, #256]
        \\ mrs x0, S3_0_C4_C0_0
        \\ str x0, [sp, #264]
        \\ stp q0, q1, [sp, #272]
        \\ stp q2, q3, [sp, #304]
        \\ stp q4, q5, [sp, #336]
        \\ stp q6, q7, [sp, #368]
        \\ stp q8, q9, [sp, #400]
        \\ stp q10, q11, [sp, #432]
        \\ stp q12, q13, [sp, #464]
        \\ stp q14, q15, [sp, #496]
        \\ add x16, sp, #512
        \\ stp q16, q17, [x16, #16]
        \\ stp q18, q19, [x16, #48]
        \\ stp q20, q21, [x16, #80]
        \\ stp q22, q23, [x16, #112]
        \\ stp q24, q25, [x16, #144]
        \\ stp q26, q27, [x16, #176]
        \\ stp q28, q29, [x16, #208]
        \\ stp q30, q31, [x16, #240]
        \\ mov x0, sp
        \\ mrs x1, esr_el1
        \\ bl %[trap]
        \\ ldp q0, q1, [sp, #272]
        \\ ldp q2, q3, [sp, #304]
        \\ ldp q4, q5, [sp, #336]
        \\ ldp q6, q7, [sp, #368]
        \\ ldp q8, q9, [sp, #400]
        \\ ldp q10, q11, [sp, #432]
        \\ ldp q12, q13, [sp, #464]
        \\ ldp q14, q15, [sp, #496]
        \\ add x16, sp, #512
        \\ ldp q16, q17, [x16, #16]
        \\ ldp q18, q19, [x16, #48]
        \\ ldp q20, q21, [x16, #80]
        \\ ldp q22, q23, [x16, #112]
        \\ ldp q24, q25, [x16, #144]
        \\ ldp q26, q27, [x16, #176]
        \\ ldp q28, q29, [x16, #208]
        \\ ldp q30, q31, [x16, #240]
        \\ ldr x0, [sp, #248]
        \\ msr S3_0_C4_C1_0, x0
        \\ ldr x0, [sp, #256]
        \\ msr S3_0_C4_C0_1, x0
        \\ ldr x0, [sp, #264]
        \\ msr S3_0_C4_C0_0, x0
        \\ ldp x0, x1, [sp, #0]
        \\ ldp x2, x3, [sp, #16]
        \\ ldp x4, x5, [sp, #32]
        \\ ldp x6, x7, [sp, #48]
        \\ ldp x8, x9, [sp, #64]
        \\ ldp x10, x11, [sp, #80]
        \\ ldp x12, x13, [sp, #96]
        \\ ldp x14, x15, [sp, #112]
        \\ ldp x16, x17, [sp, #128]
        \\ ldp x18, x19, [sp, #144]
        \\ ldp x20, x21, [sp, #160]
        \\ ldp x22, x23, [sp, #176]
        \\ ldp x24, x25, [sp, #192]
        \\ ldp x26, x27, [sp, #208]
        \\ ldp x28, x29, [sp, #224]
        \\ ldr x30, [sp, #240]
        \\ add sp, sp, #784
        \\ eret
        :
        : [trap] "S" (&loaderTrap),
        : .{ .memory = true });
}

comptime {
    asm (
        \\ .global loaderEnter
        \\ loaderEnter:
        \\ msr S3_0_C4_C0_1, x0
        \\ msr S3_0_C4_C1_0, x1
        \\ msr S3_0_C4_C0_0, xzr
        \\ mov x0, x2
        \\ mov x1, x3
        \\ mov x2, xzr
        \\ mov x3, xzr
        \\ mov x4, xzr
        \\ mov x5, xzr
        \\ mov x6, xzr
        \\ mov x7, xzr
        \\ mov x8, xzr
        \\ mov x9, xzr
        \\ mov x10, xzr
        \\ mov x11, xzr
        \\ mov x12, xzr
        \\ mov x13, xzr
        \\ mov x14, xzr
        \\ mov x15, xzr
        \\ mov x16, xzr
        \\ mov x17, xzr
        \\ mov x18, xzr
        \\ mov x19, xzr
        \\ mov x20, xzr
        \\ mov x21, xzr
        \\ mov x22, xzr
        \\ mov x23, xzr
        \\ mov x24, xzr
        \\ mov x25, xzr
        \\ mov x26, xzr
        \\ mov x27, xzr
        \\ mov x28, xzr
        \\ mov x29, xzr
        \\ mov x30, xzr
        \\ eret
    );
}
