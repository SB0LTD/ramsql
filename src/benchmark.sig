//! Native SQL benchmark: parsing, lookup and result materialization are timed.
const sql = @import("sql");
const sig = @import("sig");
const DB = sql.Database(.{ .tables = 1, .columns = 3, .rows_per_table = 512, .name_bytes = 32, .text_bytes = 48 });
var db: DB = .{};
var result: DB.Result = .{};
const iterations = 50000;

fn measure(comptime name: []const u8, query: []const u8, count: usize) !void {
    for (0..1000) |_| try db.execute(query, &result);
    const start = sig.os.clockMonotonicNs();
    var checksum: u64 = 0;
    for (0..count) |_| {
        try db.execute(query, &result);
        checksum +%= result.row_count + result.affected_rows;
        if (result.row_count != 0) checksum +%= @as(u64, @intCast(result.rows[0].cells[0].integer));
    }
    const elapsed: u64 = @intCast(sig.os.clockMonotonicNs() - start);
    if (elapsed == 0 or checksum == 0) return error.InvalidMeasurement;
    var buffer: [512]u8 = undefined;
    const line = try sig.fmt.bufPrint(&buffer, "{{\"case\":\"" ++ name ++ "\",\"iterations\":{d},\"elapsed_ns\":{d},\"ns_per_op\":{d},\"ops_per_second\":{d},\"checksum\":{d},\"rows\":512,\"database_bytes\":{d},\"result_bytes\":{d}}}\n", .{ count, elapsed, elapsed / count, @as(u64, count) * 1000000000 / elapsed, checksum, @sizeOf(DB), @sizeOf(DB.Result) });
    try sig.io.File.stdout().writeStreamingAll(.{}, line);
}

pub fn main() !void {
    try db.execute("CREATE TABLE bench (id INTEGER PRIMARY KEY, value INTEGER, label TEXT)", &result);
    var buffer: [160]u8 = undefined;
    for (0..512) |index| {
        const query = try sig.fmt.bufPrint(&buffer, "INSERT INTO bench VALUES ({d}, {d}, 'ramsql')", .{ index, index });
        try db.execute(query, &result);
    }
    try measure("primary_key_select", "SELECT id, value FROM bench WHERE id = 511", iterations);
    try measure("scan_limit_16", "SELECT id FROM bench LIMIT 16", iterations / 10);
    try measure("point_update", "UPDATE bench SET value = 7 WHERE id = 511", iterations / 10);
}
