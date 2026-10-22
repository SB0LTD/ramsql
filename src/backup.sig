//! Offline, byte-verified whole-WAL backups. No compaction or native struct dumps.
const disk = @import("db_file");

fn exact(file: *disk.File, offset: u64, buffer: []u8) !void {
    var done: usize = 0;
    while (done < buffer.len) {
        const n = try file.readAt(offset + done, buffer[done..]);
        if (n == 0 or n > buffer.len - done) return error.BackupUnexpectedEnd;
        done += n;
    }
}

/// Caller MUST hold this source's exclusive writer lock after complete framing
/// AND semantic replay validation (Persistence.init), with no concurrent commits.
/// The source remains unchanged. Destination must be NEW and is published only
/// after fsync and a byte-for-byte readback. Existing destinations are preserved.
/// scratch is caller-owned and needs at least two bytes. Uses target + ".tmp"
/// exclusively; a pre-existing temporary is preserved and causes safe failure.
/// A failed directory sync after publish has an uncertain outcome: never assume
/// that failure permits overwriting a destination on retry. Inspect it first.
pub fn create(source: *disk.File, target: []const u8, scratch: []u8) !void {
    if (source.poisoned) return error.WriterPoisoned;
    if (scratch.len < 2) return error.BackupBufferCapacity;
    const suffix = ".tmp";
    var path_buffer: [4096]u8 = undefined;
    if (target.len == 0) return error.InvalidPath;
    if (target.len > path_buffer.len - suffix.len - 1) return error.PathTooLong;
    @memcpy(path_buffer[0..target.len], target);
    @memcpy(path_buffer[target.len..][0..suffix.len], suffix);
    const temporary_path = path_buffer[0 .. target.len + suffix.len];
    const length = try source.length();
    var temporary = try disk.File.createExclusive(temporary_path);
    var opened = true;
    defer if (opened) temporary.close();
    errdefer {
        temporary.close();
        opened = false;
        // This exact file was exclusively created by this invocation.
        disk.remove(temporary_path) catch {};
    }
    var offset: u64 = 0;
    while (offset < length) {
        const count: usize = @intCast(@min(length - offset, scratch.len));
        try exact(source, offset, scratch[0..count]);
        try temporary.appendAll(scratch[0..count]);
        offset += count;
    }
    try temporary.sync();
    if (try temporary.length() != length or try source.length() != length) return error.BackupSourceChanged;
    const half = scratch.len / 2;
    offset = 0;
    while (offset < length) {
        const count: usize = @intCast(@min(length - offset, half));
        const original = scratch[0..count];
        const copied = scratch[half..][0..count];
        try exact(source, offset, original);
        try exact(&temporary, offset, copied);
        for (original, copied) |a, b| if (a != b) return error.BackupVerificationFailed;
        offset += count;
    }
    temporary.close();
    opened = false;
    try disk.publishNew(temporary_path, target);
}
