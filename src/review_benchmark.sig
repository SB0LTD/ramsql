//! Native public-API benchmark. All result cells are consumed and checked.
const std = @import("std");
const sig = @import("sig");
const sql = @import("sql");
const pages = @import("page_region");
const DB = sql.Database(.{ .external_storage = true, .tables = 1, .columns = 4, .rows_per_table = 4,
    .text_bytes = 32, .result_rows = 128, .work_rows = 512, .tx_writes = 64 });
var db: DB = .{};
var result: DB.Result = .{};
const Case = enum { primary_key_hot, filtered_limit, sort_limit, aggregate, group_aggregate, mixed };
const filtered = "SELECT id, value FROM bench WHERE category = 7 ORDER BY id LIMIT 16";
const sorted = "SELECT id, value FROM bench ORDER BY value, id LIMIT 16";
const aggregated = "SELECT COUNT(*), SUM(value) FROM bench";
const grouped = "SELECT category, COUNT(*), SUM(value) FROM bench GROUP BY category ORDER BY category";
var pk_buf: [128]u8 = undefined;
var update_buf: [128]u8 = undefined;
var pk: []const u8 = undefined;
var update: []const u8 = undefined;
fn cell(h: u64, value: u64) u64 { return (h *% 1099511628211) ^ value; }
fn answer(source: []const u8) !u64 {
    try db.execute(source, &result);
    if (result.command == .update) {
        if (result.affected_rows != 1) return error.WrongUpdateCount;
        return 1;
    }
    var digest: u64 = result.row_count;
    for (result.rows[0..result.row_count]) |*row| for (row.cells[0..result.column_count]) |*value| {
        if (value.* != .integer) return error.WrongResultType;
        digest = cell(digest, @bitCast(value.integer));
    };
    return digest;
}
fn run(which: Case) !u64 {
    return switch (which) {
        .primary_key_hot => answer(pk), .filtered_limit => answer(filtered), .sort_limit => answer(sorted),
        .aggregate => answer(aggregated), .group_aggregate => answer(grouped),
        .mixed => (try answer(pk)) +% (try answer(filtered)) +% (try answer(sorted)) +% (try answer(update)),
    };
}
fn model(which: Case, n: usize, changed: bool) u64 {
    var digest: u64 = 0;
    switch (which) {
        .primary_key_hot => return cell(cell(1, n / 2), if (changed) 7 else n - n / 2),
        .filtered_limit => {
            const count = @min(16, (n - 1 - 7) / 64 + 1);
            digest = count;
            for (0..count) |i| { const id = 7 + i * 64; digest = cell(cell(digest, id), if (changed and id == n / 2) 7 else n - id); }
        },
        .sort_limit => {
            digest = 16;
            var emitted: usize = 0;
            var value: usize = 1;
            while (emitted < 16) : (value += 1) {
                if (changed and value == 7) { digest = cell(cell(digest, n / 2), 7); emitted += 1; }
                if (emitted == 16) break;
                digest = cell(cell(digest, n - value), value); emitted += 1;
            }
        },
        .aggregate => return cell(cell(1, n), @as(u64, n) * (n + 1) / 2),
        .group_aggregate => {
            digest = 64;
            for (0..64) |g| {
                const count = (n - 1 - g) / 64 + 1;
                const sum = @as(u64, count) * n - (g * count + 64 * count * (count - 1) / 2);
                digest = cell(cell(cell(digest, g), count), sum);
            }
        },
        .mixed => return model(.primary_key_hot, n, true) +% model(.filtered_limit, n, true) +% model(.sort_limit, n, true) +% 1,
    }
    return digest;
}
fn write(line: []const u8) !void { try sig.io.File.stdout().writeStreamingAll(.{}, line); }
fn measure(which: Case, n: usize, iterations: usize) !void {
    if (which == .mixed) _ = try answer(update);
    const expected = model(which, n, which == .mixed);
    for (0..10) |_| if (try run(which) != expected) return error.WarmupMismatch;
    for (0..5) |trial| {
        var checksum: u64 = 0;
        const start = sig.os.clockMonotonicNs();
        for (0..iterations) |_| {
            const value = try run(which);
            if (value != expected) return error.ResultMismatch;
            checksum +%= value;
        }
        const elapsed: u64 = @intCast(sig.os.clockMonotonicNs() - start);
        const operations = iterations * (if (which == .mixed) @as(usize, 4) else 1);
        const streaming = which == .aggregate or which == .group_aggregate;
        const stats = db.workspace.query.stream_stats;
        var buffer: [512]u8 = undefined;
        const line = try sig.fmt.bufPrint(&buffer, "{{\"engine\":\"ramsql-native\",\"case\":\"{s}\",\"passed\":true,\"rows\":{d},\"trial\":{d},\"operations\":{d},\"elapsed_ns\":{d},\"checksum\":{d},\"expected_digest\":{d},\"stream_rows\":{d},\"stream_groups\":{d},\"stream_probes\":{d},\"group_cells\":{d},\"group_refs\":{d}}}\n", .{ @tagName(which), n, trial, operations, elapsed, checksum, expected, if (streaming) stats.rows else 0, if (streaming) stats.groups else 0, if (streaming) stats.probes else 0, if (streaming) stats.scratch_cells else 0, if (streaming) stats.scratch_refs else 0 });
        try write(line);
    }
}
pub fn main(init: std.process.Init.Minimal) !void {
    var arg_buf: [8192]u8 = undefined;
    var args = sig.process.Argv_Iterator.init(init.args.vector, &arg_buf); _ = try args.next();
    const n = if (try args.next()) |arg| try sig.fmt.parseInt(usize, arg, 10) else 512;
    if (n < 64 or n > 10000000) return error.InvalidArguments;
    const iterations = if (try args.next()) |arg| try sig.fmt.parseInt(usize, arg, 10) else if (n <= 512) @as(usize, 2000) else if (n <= 100000) @as(usize, 100) else 10;
    if (iterations == 0 or try args.next() != null) return error.InvalidArguments;
    var memory = try pages.reserve(try DB.backingBytes(n, n * 2, @max(n * 4, 512 * 32))); defer memory.close();
    try db.bindMemory(memory.bytes, n, n * 2, @max(n * 4, 512 * 32));
    const load_start = sig.os.clockMonotonicNs();
    try db.execute("CREATE TABLE bench (id INT PRIMARY KEY, value INT, category INT, label TEXT)", &result);
    var buffer: [512]u8 = undefined;
    for (0..n) |id| try db.execute(try sig.fmt.bufPrint(&buffer, "INSERT INTO bench VALUES ({d}, {d}, {d}, 'value')", .{ id, n - id, id % 64 }), &result);
    pk = try sig.fmt.bufPrint(&pk_buf, "SELECT id, value FROM bench WHERE id = {d}", .{n / 2});
    update = try sig.fmt.bufPrint(&update_buf, "UPDATE bench SET value = 7 WHERE id = {d}", .{n / 2});
    try write(try sig.fmt.bufPrint(&buffer, "{{\"engine\":\"ramsql-native\",\"rows\":{d},\"backing_bytes\":{d},\"metadata_bytes\":{d},\"cell_bytes\":{d},\"version_bytes\":{d},\"query_ref_capacity\":{d},\"load_ns\":{d}}}\n", .{ n, memory.bytes.len, @sizeOf(DB), @sizeOf(DB.Cell), @sizeOf(DB.StorageVersion), db.workspace.query.refs.len, sig.os.clockMonotonicNs() - load_start }));
    inline for (.{ Case.primary_key_hot, Case.filtered_limit, Case.sort_limit, Case.aggregate, Case.group_aggregate, Case.mixed }) |which| {
        measure(which, n, iterations) catch |err| {
            try write(try sig.fmt.bufPrint(&buffer, "{{\"engine\":\"ramsql-native\",\"case\":\"{s}\",\"rows\":{d},\"passed\":false,\"error\":\"{s}\"}}\n", .{ @tagName(which), n, @errorName(err) }));
        };
    }
}
