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

export fn _start() callconv(.naked) noreturn {
    if (options.kernel) {
        asm volatile (
            \\ adrp x0, __stack_top
            \\ add x0, x0, :lo12:__stack_top
            \\ mov sp, x0
            \\ movz x1, #0x30, lsl #16
            \\ msr cpacr_el1, x1
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
