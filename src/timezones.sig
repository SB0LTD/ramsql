//! Time-zone data policy (RAMSQL, design §4.8). ZPM decodes TZif bytes
//! (sql.tzif); RAMSQL decides where they come from and caches them.
//!
//! `--timezone-dir PATH` names a zoneinfo directory (default
//! /usr/share/zoneinfo on Linux and macOS; none on Windows and native SB0,
//! where only UTC, numeric offsets and POSIX TZ strings work). Zone names
//! must match [A-Za-z0-9_+\-/]{1,64} without a leading '/', so '.' and path
//! traversal never reach the file system. Names are case-insensitive like
//! PostgreSQL's: `<dir>/ramsql-zones.idx` (one canonical relative path per
//! TZif file, written by tools/fetch_tzdata.py) is streamed through a fixed
//! 4 KiB buffer to find the canonical spelling, which SHOW TimeZone reports;
//! without an index the exact path is tried (a case-mismatched name then
//! fails with 22023 where PostgreSQL accepts it: divergence F3). Up to
//! `capacity` zones of at most 16 KiB each are cached in caller-owned
//! storage. Sessions keep pointers into it, so a full cache only reuses an
//! entry no session setting refers to (`in_use`, supplied by the server);
//! when every entry is referenced, one more distinct zone is
//! TimeZoneCapacity (53200). Unknown or invalid files are 22023 and leave
//! no cache entry.
const sql = @import("sql");
const db_file = @import("db_file");

pub const file_capacity = 16384;
pub const index_name = "ramsql-zones.idx";

fn lower(byte: u8) u8 {
    return if (byte >= 'A' and byte <= 'Z') byte + 32 else byte;
}

fn sameInsensitive(a: []const u8, b: []const u8) bool {
    if (a.len != b.len) return false;
    for (a, b) |x, y| if (lower(x) != lower(y)) return false;
    return true;
}

pub fn validName(name: []const u8) bool {
    if (name.len == 0 or name.len > 64 or name[0] == '/') return false;
    for (name) |byte| {
        const ok = (byte >= 'A' and byte <= 'Z') or (byte >= 'a' and byte <= 'z') or (byte >= '0' and byte <= '9') or byte == '_' or byte == '+' or byte == '-' or byte == '/';
        if (!ok) return false;
    }
    return true;
}

pub fn Timezones(comptime Zone: type, comptime capacity: usize) type {
    return struct {
        const Self = @This();
        const Entry = struct {
            name: [64]u8 = undefined,
            name_len: u8 = 0,
            size: usize = 0,
            bytes: [file_capacity]u8 = undefined,
        };

        directory: ?[]const u8 = null,
        /// Whether any session setting refers to these cached bytes.
        in_use: ?*const fn ([]const u8) bool = null,
        count: usize = 0,
        entries: [capacity]Entry = undefined,

        fn joined(self: *const Self, relative: []const u8, out: *[4200]u8) ![]const u8 {
            const dir = self.directory orelse return error.ZoneUnavailable;
            if (dir.len + 1 + relative.len > out.len) return error.ZoneUnavailable;
            @memcpy(out[0..dir.len], dir);
            out[dir.len] = '/';
            @memcpy(out[dir.len + 1 ..][0..relative.len], relative);
            return out[0 .. dir.len + 1 + relative.len];
        }

        /// Canonical spelling of `name` from the index, case-insensitively.
        fn indexLookup(self: *const Self, name: []const u8, out: *[64]u8) ?usize {
            var path: [4200]u8 = undefined;
            const index_path = self.joined(index_name, &path) catch return null;
            var file = db_file.File.openReadOnly(index_path) catch return null;
            defer file.close();
            var buffer: [4096]u8 = undefined;
            var line: [64]u8 = undefined;
            var line_len: usize = 0;
            var overlong = false;
            var offset: u64 = 0;
            while (true) {
                const count = file.readAt(offset, &buffer) catch return null;
                if (count == 0) break;
                offset += count;
                for (buffer[0..count]) |byte| {
                    if (byte == '\n' or byte == '\r') {
                        if (!overlong and line_len != 0 and sameInsensitive(line[0..line_len], name)) {
                            @memcpy(out[0..line_len], line[0..line_len]);
                            return line_len;
                        }
                        line_len = 0;
                        overlong = false;
                    } else if (line_len == line.len) {
                        overlong = true;
                    } else {
                        line[line_len] = byte;
                        line_len += 1;
                    }
                }
            }
            if (!overlong and line_len != 0 and sameInsensitive(line[0..line_len], name)) {
                @memcpy(out[0..line_len], line[0..line_len]);
                return line_len;
            }
            return null;
        }

        fn cached(self: *Self, name: []const u8) ?*Entry {
            for (self.entries[0..self.count]) |*entry| {
                if (sameInsensitive(entry.name[0..entry.name_len], name)) return entry;
            }
            return null;
        }

        fn found(entry: *Entry, zone: *Zone, canonical: *[64]u8) usize {
            zone.* = .{ .kind = .tzif, .bytes = entry.bytes[0..entry.size] };
            @memcpy(canonical[0..entry.name_len], entry.name[0..entry.name_len]);
            return entry.name_len;
        }

        /// Store zone-provider hook: name -> zone and canonical spelling.
        pub fn lookup(context: ?*anyopaque, name: []const u8, zone: *Zone, canonical: *[64]u8) anyerror!usize {
            const self: *Self = @ptrCast(@alignCast(context.?));
            if (!validName(name)) return error.InvalidZoneName;
            if (self.directory == null) return error.ZoneUnavailable;
            if (self.cached(name)) |entry| return found(entry, zone, canonical);
            var spelled_buffer: [64]u8 = undefined;
            // The index is operator data; its spelling reaches the file system
            // only under the same name rule as client input.
            const indexed: ?[]const u8 = if (self.indexLookup(name, &spelled_buffer)) |length| spelled_buffer[0..length] else null;
            const spelled: []const u8 = if (indexed) |candidate| (if (validName(candidate)) candidate else name) else name;
            if (self.cached(spelled)) |entry| return found(entry, zone, canonical);
            var slot = self.count;
            if (slot == capacity) {
                const referenced = self.in_use orelse return error.TimeZoneCapacity;
                slot = for (self.entries[0..capacity], 0..) |*candidate, index| {
                    if (!referenced(candidate.bytes[0..candidate.size])) break index;
                } else return error.TimeZoneCapacity;
            }
            var path: [4200]u8 = undefined;
            var file = db_file.File.openReadOnly(try self.joined(spelled, &path)) catch return error.UnknownZone;
            defer file.close();
            const entry = &self.entries[slot];
            // A reused slot is invalid until the new file validates.
            entry.name_len = 0;
            entry.size = 0;
            var size: usize = 0;
            while (size < entry.bytes.len) {
                const count = file.readAt(size, entry.bytes[size..]) catch return error.UnknownZone;
                if (count == 0) break;
                size += count;
            }
            if (size == entry.bytes.len) {
                // Larger than one cache slot: not a zone this profile can hold.
                var probe: [1]u8 = undefined;
                if ((file.readAt(size, &probe) catch 0) != 0) return error.UnknownZone;
            }
            sql.tzif.validate(entry.bytes[0..size]) catch return error.UnknownZone;
            entry.size = size;
            @memcpy(entry.name[0..spelled.len], spelled);
            entry.name_len = @intCast(spelled.len);
            if (slot == self.count) self.count += 1;
            return found(entry, zone, canonical);
        }
    };
}
