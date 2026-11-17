//! Product-owned cluster journal. Store WAL paths are derived from durable OIDs.
const std = @import("std");
const disk = @import("db_file");
const log = @import("durable_log");

pub fn Catalog(comptime capacity: usize, comptime name_capacity: usize) type {
    return struct {
        const Self = @This();
        const kind: u16 = 7;
        const Record = enum(u8) { reserve = 1, create = 2, drop = 3 };
        const header = 9;

        pub const Entry = struct {
            oid: u32 = 0,
            owner: u32 = 0,
            name: [name_capacity]u8 = undefined,
            len: usize = 0,

            pub fn slice(self: *const Entry) []const u8 {
                return self.name[0..self.len];
            }
        };

        file: disk.File = .{},
        sequence: u64 = 0,
        high_water: u32 = 0,
        pending: u32 = 0,
        identity: ?[16]u8 = null,
        entries: [capacity]Entry = @splat(.{}),
        failed: bool = false,

        pub fn open(self: *Self, path: []const u8, create: bool) !void {
            self.file = disk.File.openExisting(path) catch |err| blk: {
                if (err != error.FileNotFound or !create) return err;
                break :blk try disk.File.createExclusive(path);
            };
            errdefer self.close();
            var scratch: [@max(header + name_capacity, 17)]u8 = undefined;
            const replayed = try log.recoverKind(&self.file, &scratch, self, &replay, kind);
            self.sequence = replayed.sequence;
        }

        pub fn close(self: *Self) void {
            self.file.close();
        }

        fn find(self: *const Self, oid: u32) ?usize {
            for (&self.entries, 0..) |*entry, i| {
                if (entry.oid == oid) return i;
            }
            return null;
        }

        fn replay(self: *Self, payload: []const u8) !void {
            if (self.identity == null) {
                if (payload.len != 17 or payload[0] != 0) return error.InvalidDatabaseCatalog;
                self.identity = payload[1..17].*;
                return;
            }
            if (payload.len < header) return error.InvalidDatabaseCatalog;
            const oid = std.mem.readInt(u32, payload[1..5], .little);
            const owner = std.mem.readInt(u32, payload[5..9], .little);
            const name = payload[header..];
            const record = std.enums.fromInt(Record, payload[0]) orelse return error.InvalidDatabaseCatalog;
            try self.apply(record, oid, owner, name, true);
        }

        fn apply(self: *Self, record: Record, oid: u32, owner: u32, name: []const u8, mutate: bool) !void {
            if (oid == 0) return error.InvalidDatabaseCatalog;
            switch (record) {
                .reserve => {
                    if (oid <= self.high_water or owner != 0 or name.len != 0) return error.InvalidDatabaseCatalog;
                    if (!mutate) return;
                    self.high_water = oid;
                    self.pending = oid;
                },
                .create => {
                    if (oid != self.pending or owner == 0 or name.len == 0 or name.len > name_capacity or
                        std.mem.indexOfScalar(u8, name, 0) != null or !std.unicode.utf8ValidateSlice(name)) return error.InvalidDatabaseCatalog;
                    for (&self.entries) |*entry| {
                        if (entry.oid != 0 and std.mem.eql(u8, entry.slice(), name)) return error.InvalidDatabaseCatalog;
                    }
                    const index = self.find(0) orelse return error.DatabaseCapacity;
                    if (!mutate) return;
                    const entry = &self.entries[index];
                    entry.oid = oid;
                    entry.owner = owner;
                    entry.len = name.len;
                    @memcpy(entry.name[0..name.len], name);
                    self.pending = 0;
                },
                .drop => {
                    if (owner != 0 or name.len != 0) return error.InvalidDatabaseCatalog;
                    const index = self.find(oid) orelse return error.InvalidDatabaseCatalog;
                    if (!mutate) return;
                    self.entries[index] = .{};
                },
            }
        }

        fn append(self: *Self, record: Record, oid: u32, owner: u32, name: []const u8) !void {
            if (self.failed) return error.WriterPoisoned;
            if (self.identity == null) return error.InvalidDatabaseCatalog;
            if (name.len > name_capacity) return error.InvalidDatabaseName;
            try self.apply(record, oid, owner, name, false);
            const sequence = std.math.add(u64, self.sequence, 1) catch return error.InvalidDatabaseCatalog;
            var payload: [header + name_capacity]u8 = undefined;
            payload[0] = @intFromEnum(record);
            std.mem.writeInt(u32, payload[1..5], oid, .little);
            std.mem.writeInt(u32, payload[5..9], owner, .little);
            @memcpy(payload[header..][0..name.len], name);
            errdefer self.failed = true;
            try log.appendKind(&self.file, sequence, payload[0 .. header + name.len], kind);
            try self.apply(record, oid, owner, name, true);
            self.sequence = sequence;
        }

        pub fn initializeIdentity(self: *Self, identity: [16]u8) !void {
            if (self.identity != null or self.sequence != 0) return error.InvalidDatabaseCatalog;
            var payload: [17]u8 = undefined;
            payload[0] = 0;
            payload[1..17].* = identity;
            errdefer self.failed = true;
            try log.appendKind(&self.file, 1, &payload, kind);
            self.identity = identity;
            self.sequence = 1;
        }

        /// Record the new OID before any file for that OID can be created.
        pub fn reserve(self: *Self, oid: u32) !void {
            try self.append(.reserve, oid, 0, &.{});
        }

        pub fn publish(self: *Self, oid: u32, owner: u32, name: []const u8) !void {
            try self.append(.create, oid, owner, name);
        }

        pub fn drop(self: *Self, oid: u32) !void {
            try self.append(.drop, oid, 0, &.{});
        }
    };
}
