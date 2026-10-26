//! Product policy and bounded single-threaded database reactor.
//!
//! One shared READ COMMITTED store serves every client; there are no
//! per-client database copies. A statement that waits for a lock parks its
//! client (Reply.blocked: nothing consumed): the client is not re-dispatched
//! until the store's wake epoch changes, one of its timers (deadlock check,
//! lock_timeout, statement_timeout) is due, or a cancel request arrives.
//! Woken clients run in wait-start order. The idle timeout is suspended
//! while a client is parked, but its socket is still read, so a disconnect
//! aborts its transaction and releases its locks. A COPY FROM row parked on
//! a key wait is already owned by the store: the reply consumes the input
//! through that row, the rest stays buffered (read gating is the
//! backpressure) and the woken client is dispatched even with no new input
//! so the parked row is re-checked first. PostgreSQL cancel keys:
//! pid = slot index + 1 with a 32-bit secret from the entropy source,
//! compared in constant time; unknown keys are ignored silently.
const sig = @import("sig");
const std = @import("std");
const profile = @import("profile");
const scram = @import("scram");
const entropy = @import("entropy");
const Persistence = @import("persistence.sig").Persistence(Raw, Store);
const sql = @import("sql");
const tcp = @import("tcp_server");
const pg = @import("postgres");
const mysql = @import("mysql");
const tds = @import("tds");
const stores = @import("db_store");
const wall_clock = @import("wall_clock");
const timezones = @import("timezones.sig");
const pages = @import("page_region");

pub const Protocol = enum { postgres, mysql, mssql };
pub const Options = struct {
    protocol: Protocol = .postgres,
    port: ?u16 = null,
    mysql_port: ?u16 = null,
    mssql_port: ?u16 = null,
    max_clients: usize = profile.clients,
    memory: bool = false,
    data: []const u8 = "ramsql.wal",
    insecure_local: bool = false,
    auth_user: []const u8 = "postgres",
    auth_verifier_file: ?[]const u8 = null,
    idle_ms: u32 = 30000,
    /// Execution checkpoints per statement; zero disables the work limit.
    max_query_work: u64 = 0,
    /// Runtime storage budget, independent of result/query workspace sizes.
    storage_rows: usize = profile.rows,
    storage_versions: ?usize = null,
    /// zoneinfo directory for named time zones (null: UTC, offsets and
    /// POSIX TZ strings only).
    timezone_dir: ?[]const u8 = default_timezone_dir,
};
pub const default_timezone_dir: ?[]const u8 = if (@import("builtin").os.tag == .linux or @import("builtin").os.tag == .macos) "/usr/share/zoneinfo" else null;
// Integer 0/1 BOOLEAN assignment is per session: the ZPM MySQL/TDS sessions
// set Store.Connection.dialect themselves; PostgreSQL keeps the default.
// Every client plus two spare slots can hold a transaction at once.
const config: sql.Config = .{
    .external_storage = true,
    .tables = profile.tables,
    .columns = profile.columns,
    .result_rows = profile.result_rows,
    .row_locks = profile.row_locks,
    .predicates = profile.predicates,
    .expr_nodes = profile.expr_nodes,
    .relations = profile.relations,
    .rows_per_table = profile.rows,
    .name_bytes = 63,
    .text_bytes = profile.text_bytes,
    .nulls_first_ascending = false,
    .integer_bits = 32,
    .versions_per_table = profile.versions,
    .tx_writes = profile.tx_writes,
    .transactions = profile.transactions,
    .work_rows = profile.work_rows,
    .summary_groups = profile.summary_groups,
    .compact_integral_scans = profile.compact_integral_scans,
};
const Raw = sql.Database(config);
const Store = stores.Store(Raw);
const DB = Store.Connection;
const Zones = timezones.Timezones(Raw.Zone, profile.timezones);
// Caller-owned zone cache (profile.timezones x 16 KiB), filled on demand.
var zones: Zones = .{};

fn refersTo(settings: anytype, bytes: []const u8) bool {
    return settings.zone.kind == .tzif and settings.zone.bytes.ptr == bytes.ptr;
}

/// Zone-cache eviction guard: any open session's settings (current, session,
/// transaction-start, startup and savepoint copies) still referring to it.
fn zoneInUse(bytes: []const u8) bool {
    for (&clients) |*client| {
        if (client.socket == tcp.invalid_socket) continue;
        const c = &client.connection;
        if (refersTo(&c.settings, bytes) or refersTo(&c.base, bytes) or refersTo(&c.tx_start, bytes) or refersTo(&c.startup, bytes)) return true;
        for (c.saved[0..c.saved_len]) |*saved| if (refersTo(&saved.current, bytes) or refersTo(&saved.base, bytes)) return true;
    }
    return false;
}

/// Wall clock for now()/CURRENT_TIMESTAMP in PostgreSQL-epoch microseconds.
fn wallClock() ?i64 {
    const unix_us = wall_clock.nowUs() catch return null;
    return sql.temporal.fromUnixUs(unix_us);
}
const PG = pg.Session(DB);
const MySQL = mysql.Session(DB);
const TDS = tds.Session(DB);
const capacity = profile.clients;
const input_capacity = 65536;
const output_capacity = @max(PG.responseCapacity(), @max(MySQL.responseCapacity(), TDS.responseCapacity())) + 32768;
const Session = union(Protocol) { postgres: PG, mysql: MySQL, mssql: TDS };
const Reply = struct { consumed: usize, written: usize, close: bool, blocked: bool, cancel: ?pg.Cancel = null };
const Client = struct {
    socket: tcp.Socket = tcp.invalid_socket,
    connection: DB = .{ .store = &store, .id = 0 },
    session: Session = .{ .postgres = .{} },
    input: [input_capacity]u8 = undefined,
    input_len: usize = 0,
    input_start: usize = 0,
    output: [output_capacity]u8 = undefined,
    output_len: usize = 0,
    output_sent: usize = 0,
    closing: bool = false,
    touched: i64 = 0,
    /// A statement of this client is parked on a lock.
    blocked: bool = false,
    cancel_secret: u32 = 0,

    fn close(self: *Client) void {
        self.connection.disconnect();
        tcp.close(self.socket);
        self.socket = tcp.invalid_socket;
        self.blocked = false;
    }

    fn dispatch(self: *Client) !void {
        const required = switch (self.session) {
            .postgres => PG.responseCapacity(),
            .mysql => MySQL.responseCapacity(),
            .mssql => TDS.responseCapacity(),
        };
        const parked_copy = self.connection.copyBlocked();
        if (self.blocked or self.output_sent != 0 or self.closing or (self.input_len == 0 and !parked_copy) or self.output.len - self.output_len < required) return;
        const input = self.input[self.input_start..][0..self.input_len];
        const output = self.output[self.output_len..];
        store.now_ns = sig.os.clockMonotonicNs();
        const reply: Reply = switch (self.session) {
            .postgres => |*session| blk: {
                const r = try session.handle(&self.connection, input, output);
                break :blk .{ .consumed = r.consumed, .written = r.written, .close = r.close, .blocked = r.blocked, .cancel = r.cancel };
            },
            .mysql => |*session| blk: {
                const r = try session.handle(&self.connection, input, output);
                break :blk .{ .consumed = r.consumed, .written = r.written, .close = r.close, .blocked = r.blocked };
            },
            .mssql => |*session| blk: {
                const r = try session.handle(&self.connection, input, output);
                break :blk .{ .consumed = r.consumed, .written = r.written, .close = r.close, .blocked = r.blocked };
            },
        };
        if (reply.consumed > self.input_len or reply.written > output.len) return error.InvalidReply;
        if (reply.blocked) {
            // Only a parked COPY row consumes input (through that row).
            if (reply.written != 0 or !self.connection.blocked() or (reply.consumed != 0 and !self.connection.copyBlocked())) return error.InvalidReply;
            self.consume(reply.consumed);
            self.blocked = true;
            return;
        }
        if (reply.cancel) |key| cancelBackend(key);
        if (reply.consumed != 0) {
            self.consume(reply.consumed);
        } else if (self.input_len == self.input.len) return error.RequestTooLarge;
        self.output_len += reply.written;
        self.closing = reply.close;
    }

    fn consume(self: *Client, count: usize) void {
        self.input_len -= count;
        self.input_start += count;
        if (self.input_len == 0) self.input_start = 0;
    }

    fn flush(self: *Client) !void {
        if (self.output_len != 0) {
            const sent = try tcp.send(self.socket, self.output[self.output_sent..self.output_len]) orelse return;
            if (sent == 0) return error.SendFailed;
            self.output_sent += sent;
            self.touched = sig.os.clockMonotonicNs();
            if (self.output_sent == self.output_len) {
                self.output_len = 0;
                self.output_sent = 0;
            }
        }
        if (self.closing and self.output_len == 0) self.close();
    }

    /// Dispatches at most 32 buffered packets, then flushes. Returns true
    /// when packets remain buffered (the reactor should poll without waiting).
    fn serve(self: *Client) bool {
        var frames: usize = 0;
        while (frames < 32) : (frames += 1) {
            const before = self.input_len;
            self.dispatch() catch {
                self.close();
                return false;
            };
            if (self.blocked or before == self.input_len or self.closing) break;
        }
        self.flush() catch {
            self.close();
            return false;
        };
        if (self.socket == tcp.invalid_socket) return false;
        return frames == 32 and self.input_len != 0 and self.output_len == 0;
    }
};
var store: Store = .{};
var persistence: Persistence = .{};
// profile.transaction_bytes sizes this recovery buffer, which Persistence also
// hands to the store as the change-record encode buffer: any record written is
// recoverable.
var recovery_buffer: [profile.transaction_bytes]u8 = undefined;
var verifier: ?scram.Verifier = null;
// Keep large caller-owned buffers out of the executable data image.
// run initializes free-slot metadata; accept initializes every live field.
var clients: [capacity]Client = undefined;
var next_id: u64 = 1;

fn sameSecret(a: u32, b: u32) bool {
    // Constant time in the secret's bits.
    return (a ^ b) == 0;
}

/// CancelRequest from another socket: pid = slot + 1. Unknown keys are ignored.
fn cancelBackend(key: pg.Cancel) void {
    if (key.pid == 0 or key.pid > capacity) return;
    const target = &clients[key.pid - 1];
    if (target.socket == tcp.invalid_socket or target.session != .postgres) return;
    if (!sameSecret(target.cancel_secret, key.secret)) return;
    target.connection.requestCancel();
}

fn monotonicClock() i64 {
    return sig.os.clockMonotonicNs();
}

var interrupt_listener: tcp.Socket = tcp.invalid_socket;
var interrupt_max_clients: usize = 0;
var next_interrupt_poll: i64 = 0;

// These slots admit only a CancelRequest header when the SQL client pool is
// full. Ordinary overflow connections retain the reactor's rejection policy.
const OverflowCancel = struct {
    socket: tcp.Socket = tcp.invalid_socket,
    bytes: [16]u8 = undefined,
    used: usize = 0,
    deadline: i64 = 0,

    fn close(self: *OverflowCancel) void {
        if (self.socket != tcp.invalid_socket) tcp.close(self.socket);
        self.socket = tcp.invalid_socket;
    }
};
var overflow_cancels: [4]OverflowCancel = @splat(.{});

fn queueOverflowCancel(socket: tcp.Socket) bool {
    for (&overflow_cancels) |*pending| if (pending.socket == tcp.invalid_socket) {
        pending.* = .{ .socket = socket, .deadline = monotonicClock() + 1000000000 };
        return true;
    };
    return false;
}

fn pollOverflowCancels(now: i64) void {
    for (&overflow_cancels) |*pending| {
        if (pending.socket == tcp.invalid_socket) continue;
        if (now >= pending.deadline) {
            pending.close();
            continue;
        }
        const n = tcp.recv(pending.socket, pending.bytes[pending.used..]) catch {
            pending.close();
            continue;
        } orelse continue;
        pending.used += n;
        if (n == 0 or (pending.used >= 8 and !pg.isCancelHeader(pending.bytes[0..pending.used]))) {
            pending.close();
            continue;
        }
        if (pg.parseCancel(pending.bytes[0..pending.used])) |key| {
            cancelBackend(key);
            pending.close();
        }
    }
}

/// Execution hook: bounded nonblocking control I/O, at most once per millisecond.
/// Preserve ordinary startup bytes for the reactor; never dispatch another SQL
/// statement or touch the executing client's shared database/session here.
fn pollInterrupts(_: ?*anyopaque, _: u64) void {
    const now = monotonicClock();
    if (now < next_interrupt_poll) return;
    next_interrupt_poll = now + 1000000;
    if (interrupt_listener != tcp.invalid_socket)
        acceptConnections(interrupt_listener, .postgres, interrupt_max_clients, 4) catch {};
    pollOverflowCancels(now);
    for (clients[0..interrupt_max_clients]) |*client| {
        if (client.socket == tcp.invalid_socket or client.session != .postgres or client.session.postgres.startup or client.closing or client.input_start != 0) continue;
        if (client.input_len < 16) {
            // Startup connections have not consumed any input yet.
            const n = tcp.recv(client.socket, client.input[client.input_len..16]) catch continue orelse continue;
            client.input_len += n;
        }
        if (pg.parseCancel(client.input[0..client.input_len])) |key| {
            cancelBackend(key);
            tcp.close(client.socket);
            client.socket = tcp.invalid_socket;
        }
    }
}

// Bounded connection setup shared by the reactor and interrupt polling. No SQL
// is dispatched here; a fresh connection owns no transaction or shared session.
fn acceptConnections(listener: tcp.Socket, protocol: Protocol, max_clients: usize, limit: usize) !void {
    for (0..limit) |_| {
        const socket = tcp.accept(listener) catch break orelse break;
        var free: ?usize = null;
        for (clients[0..max_clients], 0..) |*candidate, index| if (candidate.socket == tcp.invalid_socket) {
            free = index;
            break;
        };
        const slot = free orelse {
            if (protocol != .postgres or !queueOverflowCancel(socket)) tcp.close(socket);
            continue;
        };
        const client = &clients[slot];
        client.socket = socket;
        client.blocked = false;
        client.connection = .{ .store = &store, .id = next_id };
        next_id +%= 1;
        if (next_id == 0) next_id = 1;
        client.session = switch (protocol) {
            .postgres => .{ .postgres = .{} },
            .mysql => .{ .mysql = .{} },
            .mssql => .{ .mssql = .{} },
        };
        if (protocol == .postgres) {
            // BackendKeyData: pid = slot + 1, fresh 32-bit secret.
            var secret: [4]u8 = undefined;
            entropy.fill(&secret) catch {
                client.close();
                continue;
            };
            client.cancel_secret = std.mem.readInt(u32, &secret, .little);
            client.session.postgres.configureBackendKey(@intCast(slot + 1), client.cancel_secret);
            if (verifier) |*credential| {
                var random: [24]u8 = undefined;
                entropy.fill(&random) catch {
                    client.close();
                    continue;
                };
                var nonce: [32]u8 = undefined;
                _ = std.base64.standard.Encoder.encode(&nonce, &random);
                client.session.postgres.configureAuthentication(credential, &nonce) catch {
                    client.close();
                    continue;
                };
            }
        }
        client.input_len = 0;
        client.input_start = 0;
        client.output_len = 0;
        client.output_sent = 0;
        client.closing = false;
        client.touched = sig.os.clockMonotonicNs();
        client.output_len = switch (client.session) {
            .postgres => 0,
            .mysql => |*session| try session.begin(&client.output),
            .mssql => |*session| try session.begin(&client.output),
        };
    }
}

fn storagePlan(options: Options) !struct { versions: usize, refs: usize, bytes: usize } {
    if (options.storage_rows == 0 or options.storage_rows > 0x3fffffff) return error.InvalidServerOptions;
    const version_count = options.storage_versions orelse if (options.storage_rows == profile.rows) profile.versions else options.storage_rows * 2;
    if (version_count <= options.storage_rows or version_count > 0x7fffffff) return error.InvalidServerOptions;
    const ref_count = @max(options.storage_rows * 4, profile.work_rows * 32);
    return .{ .versions = version_count, .refs = ref_count, .bytes = try Raw.backingBytes(options.storage_rows, version_count, ref_count) };
}

pub fn describe(options: Options) !void {
    if (options.max_clients == 0 or options.max_clients > capacity or options.idle_ms == 0) return error.InvalidServerOptions;
    const plan = try storagePlan(options);
    var buffer: [4096]u8 = undefined;
    const message = try sig.fmt.bufPrint(&buffer,
        "{{\"schema\":1,\"version\":\"{s}\",\"release_stage\":\"beta\",\"production_ready\":false,\"default_protocol\":\"postgres\",\"bind\":\"127.0.0.1\",\"tls\":false,\"roles\":false,\"isolation\":\"read_committed\",\"workers\":1,\"rows_per_table\":{d},\"versions_per_table\":{d},\"tables\":{d},\"columns\":{d},\"result_rows\":{d},\"work_rows\":{d},\"text_bytes\":{d},\"name_bytes\":{d},\"clients\":{d},\"compiled_clients\":{d},\"transaction_bytes\":{d},\"tx_writes\":{d},\"row_locks\":{d},\"predicates\":{d},\"expr_nodes\":{d},\"expr_depth\":{d},\"relations\":{d},\"subqueries\":{d},\"ctes\":{d},\"savepoints\":{d},\"sequences\":{d},\"numeric_digits\":{d},\"json_depth\":{d},\"copy_line_bytes\":{d},\"pg_packet_bytes\":{d},\"pg_prepared_sql_bytes\":{d},\"pg_statements\":{d},\"pg_portals\":{d},\"pg_parameters\":{d},\"input_bytes\":{d},\"output_bytes_per_client\":{d},\"backing_bytes\":{d},\"summary_groups\":{d},\"compact_integral_scans\":{s},\"wal_checkpointing\":false,\"mysql_tds_authentication\":false}}\n",
        .{ @import("ramsql").version, options.storage_rows, plan.versions, config.tables, config.columns, config.result_rows, config.work_rows, config.text_bytes, config.name_bytes, options.max_clients, capacity, profile.transaction_bytes, config.tx_writes, config.row_locks, config.predicates, config.expr_nodes, config.expr_depth, config.relations, config.subqueries, config.ctes, config.savepoints, config.sequences, config.numeric_digits, config.json_depth, config.copy_line_bytes, pg.max_packet, pg.sql_capacity, pg.statement_capacity, pg.portal_capacity, pg.parameter_capacity, input_capacity, output_capacity, plan.bytes, config.summary_groups, if(config.compact_integral_scans) "true" else "false" });
    try sig.io.File.stdout().writeStreamingAll(.{}, message);
}

fn openStorage(options: Options) !pages.Region {
    const plan = try storagePlan(options);
    var backing = try pages.reserve(plan.bytes);
    errdefer backing.close();
    try store.database.bindMemory(backing.bytes, options.storage_rows, plan.versions, plan.refs);
    return backing;
}

pub fn run(options: Options) !void {
    if (options.max_clients == 0 or options.max_clients > capacity or options.idle_ms == 0) return error.InvalidServerOptions;
    for (&clients) |*client| client.socket = tcp.invalid_socket;
    var backing = try openStorage(options);
    defer backing.close();
    if (wall_clock.available) store.wall_clock = &wallClock;
    store.monotonic_clock = &monotonicClock;
    store.max_query_work = options.max_query_work;
    store.interrupt_poll = &pollInterrupts;
    interrupt_max_clients = options.max_clients;
    zones.directory = options.timezone_dir;
    zones.in_use = &zoneInUse;
    store.zone_provider = .{ .context = @ptrCast(&zones), .lookup = &Zones.lookup };
    if (!options.insecure_local) {
        const verifier_path = options.auth_verifier_file orelse return error.AuthenticationRequired;
        if (options.protocol != .postgres or options.mysql_port != null or options.mssql_port != null) return error.AuthenticationUnsupportedForProtocol;
        const file = try sig.io.Dir.cwd().openFile(.{}, verifier_path, .{});
        defer file.close(.{});
        var source: [513]u8 = undefined;
        var reader = file.reader(.{}, &.{});
        var used: usize = 0;
        while (used < source.len) {
            const n = try reader.interface.readSliceShort(source[used..]);
            if (n == 0) break;
            used += n;
        }
        if (used == 0 or used > 512) return error.InvalidVerifier;
        while (used != 0 and (source[used - 1] == '\n' or source[used - 1] == '\r')) used -= 1;
        verifier = try scram.Verifier.parse(options.auth_user, source[0..used]);
        std.crypto.secureZero(u8, &source);
    } else if (options.auth_verifier_file != null) return error.InvalidServerOptions;
    if (!options.memory) {
        _ = try persistence.init(options.data, &store, &recovery_buffer);
    }
    defer persistence.close();
    try tcp.init();
    defer tcp.deinit();
    var listeners: [3]tcp.Socket = @splat(tcp.invalid_socket);
    var protocols: [3]Protocol = @splat(options.protocol);
    var count: usize = 1;
    const default_port: u16 = switch (options.protocol) {
        .postgres => 5432,
        .mysql => 3306,
        .mssql => 1433,
    };
    listeners[0] = try tcp.listen(.{ 127, 0, 0, 1 }, options.port orelse default_port);
    defer for (listeners[0..count]) |listener| tcp.close(listener);
    if (options.mysql_port) |port| {
        protocols[count] = .mysql;
        listeners[count] = try tcp.listen(.{ 127, 0, 0, 1 }, port);
        count += 1;
    }
    if (options.mssql_port) |port| {
        protocols[count] = .mssql;
        listeners[count] = try tcp.listen(.{ 127, 0, 0, 1 }, port);
        count += 1;
    }
    interrupt_listener = if (options.protocol == .postgres) listeners[0] else tcp.invalid_socket;
    defer interrupt_listener = tcp.invalid_socket;
    defer for (&overflow_cancels) |*pending| pending.close();
    for (listeners[0..count], protocols[0..count]) |listener, protocol| {
        var buffer: [128]u8 = undefined;
        const line = try sig.fmt.bufPrint(&buffer, "RAMSQL {s} 127.0.0.1:{d}\n", .{ @tagName(protocol), try tcp.localPort(listener) });
        try sig.io.File.stdout().writeStreamingAll(.{}, line);
    }
    defer for (&clients) |*client| if (client.socket != tcp.invalid_socket) {
        client.close();
    };
    // Probe once at startup, then accept only after readiness. Queues that
    // exceed the bounded accept batch stay readable on the next poll. The
    // separate execution hook still accepts CancelRequests during long SQL.
    var listener_ready: [3]bool = @splat(true);
    while (true) {
        // Bound accept work so connection churn cannot monopolize the reactor.
        for (listeners[0..count], protocols[0..count], listener_ready[0..count]) |listener, protocol, ready| {
            if (ready) try acceptConnections(listener, protocol, options.max_clients, capacity);
        }
        pollOverflowCancels(sig.os.clockMonotonicNs());
        var sockets: [capacity + 2]tcp.Socket = @splat(tcp.invalid_socket);
        var readable: [capacity + 2]bool = @splat(false);
        var writable: [capacity + 2]bool = @splat(false);
        const now = sig.os.clockMonotonicNs();
        store.now_ns = now;
        var buffered = false;
        // Wake parked clients whose lock may have been released, whose timer
        // is due or that were canceled, in wait-start (FIFO) order.
        var woken: [capacity]usize = undefined;
        var woken_count: usize = 0;
        for (clients[0..options.max_clients], 0..) |*client, index| {
            if (client.socket == tcp.invalid_socket or !client.blocked or !client.connection.wakeReady()) continue;
            var position = woken_count;
            while (position != 0 and clients[woken[position - 1]].connection.waitStart() > client.connection.waitStart()) : (position -= 1) woken[position] = woken[position - 1];
            woken[position] = index;
            woken_count += 1;
        }
        for (woken[0..woken_count]) |index| {
            const client = &clients[index];
            if (client.socket == tcp.invalid_socket) continue;
            client.blocked = false;
            if (client.serve()) buffered = true;
        }
        for (clients[0..options.max_clients], 0..) |*client, index| {
            if (client.socket == tcp.invalid_socket) continue;
            // The idle timeout does not apply while a statement waits.
            if (client.blocked) client.touched = now;
            if (now - client.touched >= @as(i64, options.idle_ms) * 1000000) {
                client.close();
                continue;
            }
            if (client.serve()) buffered = true;
            if (client.socket == tcp.invalid_socket) continue;
            sockets[index] = client.socket;
            readable[index] = client.output_len == 0 and client.input_len < client.input.len;
            writable[index] = client.output_len != 0;
        }
        for (1..count) |index| {
            sockets[capacity + index - 1] = listeners[index];
            readable[capacity + index - 1] = true;
        }
        // Poll timeout: the idle bound or the nearest parked-statement timer.
        var timeout: u32 = @min(options.idle_ms, 1000);
        for (clients[0..options.max_clients]) |*client| {
            if (client.socket == tcp.invalid_socket or !client.blocked) continue;
            if (client.connection.wakeReady()) {
                timeout = 0;
                continue;
            }
            const due = client.connection.nextDeadline() orelse continue;
            const remaining = due - now;
            const ms: u32 = if (remaining <= 0) 0 else @intCast(@min(@divFloor(remaining + 999999, 1000000), timeout));
            timeout = @min(timeout, ms);
        }
        listener_ready[0] = try tcp.waitMasked(listeners[0], &sockets, &readable, &writable, if (buffered) 0 else timeout);
        for (1..count) |index| listener_ready[index] = readable[capacity + index - 1];
        for (clients[0..options.max_clients], 0..) |*client, index| {
            if (client.socket == tcp.invalid_socket) continue;
            if (writable[index]) {
                client.flush() catch {
                    client.close();
                    continue;
                };
            }
            if (client.socket == tcp.invalid_socket) continue;
            if (readable[index] and client.output_len == 0) {
                // Compact once per socket read, not once per pipelined packet.
                if (client.input_start != 0) {
                    for (0..client.input_len) |offset| client.input[offset] = client.input[client.input_start + offset];
                    client.input_start = 0;
                }
                if (tcp.recv(client.socket, client.input[client.input_len..]) catch {
                    client.close();
                    continue;
                }) |received| {
                    if (received == 0) {
                        client.close();
                        continue;
                    }
                    client.input_len += received;
                    client.touched = sig.os.clockMonotonicNs();
                }
            }
        }
    }
}

/// Offline backup validates/replays the existing source before publishing a copy.
pub fn backup(options: Options, target: []const u8) !void {
    if (options.memory) return error.InvalidServerOptions;
    var backing = try openStorage(options);
    defer backing.close();
    _ = try persistence.initExisting(options.data, &store, &recovery_buffer);
    defer persistence.close();
    try @import("backup.sig").create(&persistence.file, target, &recovery_buffer);
}
