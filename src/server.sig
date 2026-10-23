//! Product policy and bounded single-threaded database reactor.
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
};
// Integer 0/1 BOOLEAN assignment is per session: the ZPM MySQL/TDS sessions
// set Store.Connection.dialect themselves; PostgreSQL keeps the default.
const Raw = sql.Database(.{ .tables = profile.tables, .columns = 8, .rows_per_table = profile.rows, .name_bytes = 63, .text_bytes = profile.text_bytes, .nulls_first_ascending = false, .integer_bits = 32 });
const Store = stores.Store(Raw);
const DB = Store.Connection;
const PG = pg.Session(DB);
const MySQL = mysql.Session(DB);
const TDS = tds.Session(DB);
const capacity = profile.clients;
const input_capacity = 65536;
const output_capacity = @max(PG.responseCapacity(), @max(MySQL.responseCapacity(), TDS.responseCapacity())) + 32768;
const Session = union(Protocol) { postgres: PG, mysql: MySQL, mssql: TDS };
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
    transaction_workspace: Raw = undefined,
    transaction_log: [profile.transaction_bytes]u8 = undefined,

    fn close(self: *Client) void {
        self.connection.disconnect();
        tcp.close(self.socket);
        self.socket = tcp.invalid_socket;
    }

    fn dispatch(self: *Client) !void {
        const required = switch (self.session) {
            .postgres => PG.responseCapacity(),
            .mysql => MySQL.responseCapacity(),
            .mssql => TDS.responseCapacity(),
        };
        if (self.output_sent != 0 or self.closing or self.input_len == 0 or self.output.len - self.output_len < required) return;
        const input = self.input[self.input_start..][0..self.input_len];
        const output = self.output[self.output_len..];
        const reply = switch (self.session) {
            .postgres => |*session| blk: {
                const r = try session.handle(&self.connection, input, output);
                break :blk pg.Reply{ .consumed = r.consumed, .written = r.written, .close = r.close };
            },
            .mysql => |*session| blk: {
                const r = try session.handle(&self.connection, input, output);
                break :blk pg.Reply{ .consumed = r.consumed, .written = r.written, .close = r.close };
            },
            .mssql => |*session| blk: {
                const r = try session.handle(&self.connection, input, output);
                break :blk pg.Reply{ .consumed = r.consumed, .written = r.written, .close = r.close };
            },
        };
        if (reply.consumed > self.input_len or reply.written > output.len) return error.InvalidReply;
        if (reply.consumed != 0) {
            self.input_len -= reply.consumed;
            self.input_start += reply.consumed;
            if (self.input_len == 0) self.input_start = 0;
        } else if (self.input_len == self.input.len) return error.RequestTooLarge;
        self.output_len += reply.written;
        self.closing = reply.close;
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
};
var store: Store = .{};
var autocommit_workspace: Raw = undefined;
var persistence: Persistence = .{};
// profile.transaction_bytes sizes every client transaction_log (kind-3 change
// record encode buffer) and this recovery buffer, which Persistence also hands
// to the store as the autocommit encode buffer: any record written is recoverable.
var recovery_buffer: [profile.transaction_bytes]u8 = undefined;
var verifier: ?scram.Verifier = null;
// Keep large caller-owned workspaces/buffers out of the executable data image.
// run initializes free-slot metadata; accept initializes every live field.
var clients: [capacity]Client = undefined;
var next_id: u64 = 1;

pub fn run(options: Options) !void {
    if (options.max_clients == 0 or options.max_clients > capacity or options.idle_ms == 0) return error.InvalidServerOptions;
    for (&clients) |*client| client.socket = tcp.invalid_socket;
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
        _ = try persistence.init(options.data, &store, &autocommit_workspace, &recovery_buffer);
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
    for (listeners[0..count], protocols[0..count]) |listener, protocol| {
        var buffer: [128]u8 = undefined;
        const line = try sig.fmt.bufPrint(&buffer, "RAMSQL {s} 127.0.0.1:{d}\n", .{ @tagName(protocol), try tcp.localPort(listener) });
        try sig.io.File.stdout().writeStreamingAll(.{}, line);
    }
    defer for (&clients) |*client| if (client.socket != tcp.invalid_socket) {
        client.close();
    };
    while (true) {
        // Bound accept work so connection churn cannot monopolize the reactor.
        for (listeners[0..count], protocols[0..count]) |listener, protocol| {
            for (0..capacity) |_| {
                const socket = tcp.accept(listener) catch break orelse break;
                var free: ?*Client = null;
                for (clients[0..options.max_clients]) |*candidate| if (candidate.socket == tcp.invalid_socket) {
                    free = candidate;
                    break;
                };
                const client = free orelse {
                    tcp.close(socket);
                    continue;
                };
                client.socket = socket;
                client.connection = .{ .store = &store, .id = next_id, .transaction_workspace = &client.transaction_workspace, .transaction_log = &client.transaction_log };
                next_id +%= 1;
                if (next_id == 0) next_id = 1;
                client.session = switch (protocol) {
                    .postgres => .{ .postgres = .{} },
                    .mysql => .{ .mysql = .{} },
                    .mssql => .{ .mssql = .{} },
                };
                if (protocol == .postgres) {
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
        var sockets: [capacity + 2]tcp.Socket = @splat(tcp.invalid_socket);
        var readable: [capacity + 2]bool = @splat(false);
        var writable: [capacity + 2]bool = @splat(false);
        const now = sig.os.clockMonotonicNs();
        var buffered = false;
        for (clients[0..options.max_clients], 0..) |*client, index| {
            if (client.socket == tcp.invalid_socket) continue;
            if (now - client.touched >= @as(i64, options.idle_ms) * 1000000) {
                client.close();
                continue;
            }
            // Process at most 32 buffered packets per client before yielding.
            var frames: usize = 0;
            while (frames < 32) : (frames += 1) {
                const before = client.input_len;
                client.dispatch() catch {
                    client.close();
                    break;
                };
                if (before == client.input_len or client.closing) break;
            }
            client.flush() catch {
                client.close();
                continue;
            };
            if (client.socket == tcp.invalid_socket) continue;
            if (frames == 32 and client.input_len != 0 and client.output_len == 0) buffered = true;
            sockets[index] = client.socket;
            readable[index] = client.output_len == 0 and client.input_len < client.input.len;
            writable[index] = client.output_len != 0;
        }
        for (1..count) |index| {
            sockets[capacity + index - 1] = listeners[index];
            readable[capacity + index - 1] = true;
        }
        _ = try tcp.waitMasked(listeners[0], &sockets, &readable, &writable, if (buffered) 0 else @min(options.idle_ms, 1000));
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
    _ = try persistence.initExisting(options.data, &store, &autocommit_workspace, &recovery_buffer);
    defer persistence.close();
    try @import("backup.sig").create(&persistence.file, target, &recovery_buffer);
}
