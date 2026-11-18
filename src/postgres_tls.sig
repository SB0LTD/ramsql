//! Owns one caller-backed TLS transport for negotiated PostgreSQL SSLRequest.
const std = @import("std");
const sig = @import("sig");
const pg = @import("postgres");
const tcp = @import("tcp_server");
const tls = @import("postgres_tls_stream");

const record_plaintext_bytes = 16384;
const max_leaf_bytes = 16384;
const flight_bytes = max_leaf_bytes + 512;
const cipher_output_bytes = @max(tls.record_capacity, flight_bytes + 512);
const Identity = @TypeOf(@as(tls.Stream, undefined).handshake.identity.*);

pub const Credentials = struct {
    certificate: [max_leaf_bytes + 1]u8 = undefined,
    chain: [1][]const u8 = undefined,
    identity: Identity = undefined,

    pub fn load(self: *Credentials, certificate_path: []const u8, key_path: []const u8) !void {
        const length = try readFile(certificate_path, &self.certificate);
        if (length == 0 or length > max_leaf_bytes) return error.InvalidTlsCertificate;
        var key: [33]u8 = undefined;
        defer std.crypto.secureZero(u8, &key);
        if (try readFile(key_path, &key) != 32) return error.InvalidTlsPrivateKey;
        self.chain[0] = self.certificate[0..length];
        self.identity = try Identity.init(&self.chain, key[0..32]);
    }

    pub fn close(self: *Credentials) void {
        std.crypto.secureZero(u8, std.mem.asBytes(&self.identity));
    }
};

fn readFile(path: []const u8, buffer: []u8) !usize {
    const file = try sig.io.Dir.cwd().openFile(.{}, path, .{});
    defer file.close(.{});
    var reader = file.reader(.{}, &.{});
    return reader.interface.readSliceShort(buffer);
}

pub const Slot = struct {
    transport: Transport,
    receive: [tls.record_capacity]u8,
    send: [cipher_output_bytes]u8,
    hello: [32768]u8,
    flight: [flight_bytes]u8,
    plain: [record_plaintext_bytes]u8,

    pub fn open(self: *Slot, credentials: *const Credentials) !*Transport {
        self.transport = .{
            .stream = try tls.Stream.init(&credentials.identity, .{
                .receive = &self.receive, .send = &self.send, .hello = &self.hello, .flight = &self.flight,
            }),
            .plain = &self.plain,
        };
        return &self.transport;
    }
};

const Phase = enum { ssl_request, ssl_response, handshake, open };

pub const Transport = struct {
    stream: tls.Stream,
    plain: []u8,
    phase: Phase = .ssl_request,
    prefix: [8]u8 = undefined,
    prefix_used: usize = 0,
    plain_start: usize = 0,
    plain_end: usize = 0,
    peer_closed: bool = false,
    close_queued: bool = false,
    more: bool = false,

    pub fn established(self: *const Transport) bool {
        return self.phase == .open;
    }

    pub fn canRead(self: *const Transport) bool {
        return self.phase != .ssl_response and !self.peer_closed and !self.stream.failed and
            self.plain_start == self.plain_end and self.stream.output().len == 0;
    }

    pub fn needsWrite(self: *const Transport) bool {
        return self.phase == .ssl_response or self.stream.output().len != 0;
    }

    pub fn receive(self: *Transport, socket: tcp.Socket) !bool {
        if (!self.canRead()) return false;
        const space = if (self.phase == .ssl_request) self.prefix[self.prefix_used..] else self.stream.input();
        if (space.len == 0) return error.TlsInputFull;
        const count = try tcp.recv(socket, space) orelse return false;
        if (count == 0) return error.EndOfStream;
        if (self.phase == .ssl_request) {
            self.prefix_used += count;
            if (self.prefix_used == self.prefix.len) {
                switch (pg.classifyStartup(&self.prefix)) {
                    .ssl_request => self.phase = .ssl_response,
                    else => return error.TlsRequired,
                }
            }
        } else try self.stream.received(count);
        return true;
    }

    /// Copy plaintext into the PG frame buffer. Remainders are owned by this
    /// slot, so no borrowed stream data survives a later TLS mutation.
    pub fn pull(self: *Transport, destination: []u8) !usize {
        self.more = false;
        if (self.phase == .ssl_request or self.phase == .ssl_response or self.peer_closed) return 0;
        var written: usize = 0;
        for (0..32) |_| {
            if (self.plain_start != self.plain_end) {
                const count = @min(self.plain_end - self.plain_start, destination.len - written);
                @memcpy(destination[written..][0..count], self.plain[self.plain_start..][0..count]);
                std.crypto.secureZero(u8, self.plain[self.plain_start..][0..count]);
                written += count;
                self.plain_start += count;
                if (self.plain_start != self.plain_end) break;
                self.plain_start = 0;
                self.plain_end = 0;
            }
            if (self.phase == .open and written == destination.len) break;
            switch (try self.stream.next()) {
                .need_input, .need_output => return written,
                .progress => {},
                .established => self.phase = .open,
                .close_notify => {
                    self.peer_closed = true;
                    return written;
                },
                .data => |data| {
                    const count = @min(data.len, destination.len - written);
                    @memcpy(destination[written..][0..count], data[0..count]);
                    written += count;
                    const remainder = data[count..];
                    @memcpy(self.plain[0..remainder.len], remainder);
                    self.plain_end = remainder.len;
                },
            }
        }
        self.more = true;
        return written;
    }

    pub const Flush = struct { accepted: usize = 0, sent: bool = false };

    /// Accept one protected application fragment only after any pending wire
    /// output has drained. The caller may then retire exactly accepted bytes.
    pub fn flush(self: *Transport, socket: tcp.Socket, output: []const u8, closing: bool) !Flush {
        var result: Flush = .{};
        if (self.phase == .ssl_response) {
            const count = try tcp.send(socket, "S") orelse return result;
            if (count != 1) return error.SendFailed;
            self.phase = .handshake;
            result.sent = true;
            return result;
        }
        if (self.established() and self.stream.output().len == 0) {
            if (output.len != 0) {
                result.accepted = try self.stream.queueData(output);
            } else if (closing and !self.close_queued) {
                try self.stream.queueAlert(0);
                self.close_queued = true;
            }
        }
        const pending = self.stream.output();
        if (pending.len != 0) {
            const count = try tcp.send(socket, pending) orelse return result;
            if (count == 0) return error.SendFailed;
            try self.stream.sent(count);
            result.sent = true;
        }
        return result;
    }

    pub fn close(self: *Transport) void {
        self.stream.close();
        std.crypto.secureZero(u8, self.plain);
    }
};
