//! Native SB0 PostgreSQL protocol process. The same SQL/store/session code is
//! used on hosted systems; only the delegated channel / privileged UART differs.
//! This explicit memory-only, trusted transport profile has no TCP listener,
//! durable WAL or SCRAM entropy source. It is not a production database image.
const sql = @import("sql");
const stores = @import("db_store");
const postgres = @import("postgres");
const streams = @import("stream_server");
const native = @import("sb0_stream");
const options = @import("sb0_options");
const Raw = sql.Database(.{ .tables = 8, .columns = 8, .rows_per_table = 512, .name_bytes = 63, .text_bytes = 128, .nulls_first_ascending = false, .integer_bits = 32 });
const Store = stores.Store(Raw);
const Session = postgres.Session(Store.Connection);
const Stream = streams.Server(Store.Connection, Session, 65536);
// One shared READ COMMITTED store; the single connection holds no database copy.
var store: Store = .{};
var connection: Store.Connection = .{ .store = &store, .id = 1 };
var session: Session = .{};
var buffers: Stream = .{};
extern var __bss_start: u8;
extern var __bss_end: u8;
extern const __stack_top: u8;

export fn nativeMain() callconv(.c) noreturn {
    if (options.kernel) {
        const start: [*]u8 = @ptrCast(&__bss_start);
        const length = @intFromPtr(&__bss_end) - @intFromPtr(&__bss_start);
        for (0..length) |i| start[i] = 0;
        // These initialized pointers live in .data, outside the cleared BSS.
        var io: native.Uart = .{};
        io.writeAll("RAMSQL-SB0K-READY memory-only postgres UART\r\n") catch {};
        buffers.run(&io, &connection, &session) catch {
            connection.disconnect();
            io.writeAll("RAMSQL-SB0K-ERROR\r\n") catch {};
            native.park();
        };
        connection.disconnect();
        io.writeAll("RAMSQL-SB0K-EXIT\r\n") catch {};
        native.park();
    } else {
        var io: native.Channel = .{};
        native.debug("RAMSQL-SB0X-READY memory-only postgres channel\n");
        buffers.run(&io, &connection, &session) catch {
            connection.disconnect();
            native.debug("RAMSQL-SB0X-ERROR\n");
            native.processExit(1);
        };
        connection.disconnect();
        native.processExit(0);
    }
}

// EL1 exception vectors for SB0K. Without them any synchronous fault (an
// unaligned Device access, an svc, a bad pointer) left the kernel hung with no
// output. Every vector reports the syndrome over the UART and parks, so the
// QEMU transcript shows what failed and where.
// One 128-byte vector entry: branch to the reporter, then pad with nops. The
// table is a naked function so only inline asm is needed (no module-level
// asm directives, which this assembler does not accept).
const vector_entry = " b %[fault]\n" ++ (" nop\n" ** 31);

export fn sb0kVectors() align(2048) callconv(.naked) noreturn {
    asm volatile (vector_entry ** 16
        :
        : [fault] "S" (&sb0kFault),
    );
}

fn hex(out: []u8, at: *usize, label: []const u8, value: u64) void {
    for (label) |c| {
        out[at.*] = c;
        at.* += 1;
    }
    var shift: u6 = 60;
    while (true) : (shift -= 4) {
        const digit: u8 = @as(u8, @truncate(value >> shift)) & 15;
        out[at.*] = if (digit < 10) '0' + digit else 'a' + digit - 10;
        at.* += 1;
        if (shift == 0) break;
    }
}

export fn sb0kFault() callconv(.c) noreturn {
    // Generic system-register encodings: ESR_EL1, ELR_EL1 and FAR_EL1.
    const esr = asm volatile ("mrs %[r], s3_0_c5_c2_0"
        : [r] "=r" (-> u64),
    );
    const elr = asm volatile ("mrs %[r], s3_0_c4_c0_1"
        : [r] "=r" (-> u64),
    );
    const far = asm volatile ("mrs %[r], s3_0_c6_c0_0"
        : [r] "=r" (-> u64),
    );
    var line: [96]u8 = undefined;
    var at: usize = 0;
    hex(&line, &at, "\r\nRAMSQL-SB0K-FAULT esr=0x", esr);
    hex(&line, &at, " elr=0x", elr);
    hex(&line, &at, " far=0x", far);
    line[at] = '\r';
    line[at + 1] = '\n';
    var io: native.Uart = .{};
    io.writeAll(line[0 .. at + 2]) catch {};
    native.park();
}

// SB0K turns the MMU on before entering Sig code. With the MMU off every
// access is Device memory, where an unaligned load faults. The native compiler
// emits unaligned loads for byte arrays and align(1) data even with
// strict_align (an alignment fault at ldr w3,[sp,#0x141] under QEMU 10.2), and
// real hardware faults the same way. This identity map uses 1 GiB blocks:
// Device-nGnRnE below 0x40000000 (the PL011 UART at 0x09000000), and Normal
// write-back memory from 0x40000000 for the image, data and stack. _start
// programs MAIR_EL1 (attribute 0 = 0x00 Device, attribute 1 = 0xff Normal),
// TCR_EL1 0x803519 (T0SZ 25, write-back walks, inner shareable, 4 KiB granule,
// TTBR1 walks disabled) and TTBR0_EL1, then sets SCTLR_EL1.M/C/I with A clear.
export var sb0kTranslation: [512]u64 align(4096) = blk: {
    var table = [_]u64{0} ** 512;
    // Block, AF, AttrIndx 0 (Device), UXN and PXN.
    table[0] = 0x0000_0000 | (1 << 54) | (1 << 53) | (1 << 10) | (0 << 2) | 0b01;
    // Block, AF, inner shareable, AttrIndx 1 (Normal write-back).
    table[1] = 0x4000_0000 | (1 << 10) | (3 << 8) | (1 << 2) | 0b01;
    break :blk table;
};

export fn _start() callconv(.naked) noreturn {
    if (options.kernel) {
        asm volatile (
            \\ adrp x0, __stack_top
            \\ add x0, x0, :lo12:__stack_top
            \\ mov sp, x0
            \\ movz x1, #0x30, lsl #16
            \\ msr cpacr_el1, x1
            \\ adrp x1, sb0kVectors
            \\ add x1, x1, :lo12:sb0kVectors
            \\ msr vbar_el1, x1
            \\ movz x1, #0xff00
            \\ msr s3_0_c10_c2_0, x1
            \\ movz x1, #0x3519
            \\ movk x1, #0x80, lsl #16
            \\ msr s3_0_c2_c0_2, x1
            \\ adrp x1, sb0kTranslation
            \\ add x1, x1, :lo12:sb0kTranslation
            \\ msr s3_0_c2_c0_0, x1
            \\ dsb sy
            \\ isb
            \\ mrs x1, s3_0_c1_c0_0
            \\ movz x2, #0x1007
            \\ orr x1, x1, x2
            \\ movz x2, #0x2
            \\ eor x1, x1, x2
            \\ msr s3_0_c1_c0_0, x1
            \\ isb
            \\ bl %[main]
            \\ b .
            :
            : [main] "S" (&nativeMain),
            : .{ .memory = true });
    } else {
        asm volatile (
            \\ bl %[main]
            \\ mov x8, #0
            \\ mov x0, #1
            \\ svc #0
            \\ 1: wfe
            \\ b 1b
            :
            : [main] "S" (&nativeMain),
            : .{ .memory = true });
    }
    unreachable;
}
