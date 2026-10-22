//! Durable product/store integration contracts; executable assertion count.
const std = @import("std");
const builtin = @import("builtin");
const sql = @import("sql");
const stores = @import("db_store");
const disk = @import("db_file");
const log = @import("durable_log");
const module = @import("persistence");
const Raw = sql.Database(.{ .tables = 4, .columns = 3, .rows_per_table = 16, .text_bytes = 64 });
const Store = stores.Store(Raw);
const Persistence = module.Persistence(Raw, Store);
const Win = struct {
    extern "kernel32" fn GetCurrentProcessId() callconv(.winapi) u32;
};
var assertions: usize = 0;
fn check(ok: bool) !void {
    assertions += 1;
    if (!ok) return error.PersistenceContractFailed;
}
fn addFrame(buffer: []u8, position: *usize, statement: []const u8) void {
    for (0..4) |i| buffer[position.* + i] = @truncate(statement.len >> @intCast(i * 8));
    position.* += 4;
    @memcpy(buffer[position.*..][0..statement.len], statement);
    position.* += statement.len;
}
fn clearLog(path: []const u8) !void {
    var file = try disk.File.open(path);
    defer file.close();
    try file.truncate(0);
    try file.sync();
}
pub fn main(init: std.process.Init) !void {
    const pid: u32 = if (builtin.os.tag == .windows) Win.GetCurrentProcessId() else @intCast(std.os.linux.getpid());
    var path_buffer: [256]u8 = undefined;
    const path = try std.fmt.bufPrint(&path_buffer, "sig-out/persistence-contract-{d}.wal", .{pid});
    var created = try disk.File.createExclusive(path);
    created.close();
    defer disk.remove(path) catch {};
    var store: Store = .{};
    var workspace: Raw = .{};
    var tx_workspace: Raw = .{};
    var tx_log: [4096]u8 = undefined;
    var recovery_buffer: [4096]u8 = undefined;
    var persistence: Persistence = .{};
    defer persistence.close();
    const empty = try persistence.init(path, &store, &workspace, &recovery_buffer);
    try check(empty.sequence == 0 and !empty.truncated_tail and store.durability_required);
    var connection: Store.Connection = .{ .store = &store, .id = 1, .transaction_workspace = &tx_workspace, .transaction_log = &tx_log };
    var result: Raw.Result = .{};
    try connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)", &result);
    try connection.execute("INSERT INTO t VALUES (1, 'first')", &result);
    try check(persistence.sequence == 2 and store.generation == 2);
    try connection.beginTransaction();
    try connection.execute("UPDATE t SET value = 'committed; newline' WHERE id = 1", &result);
    try connection.execute("INSERT INTO t VALUES (2, 'second')", &result);
    try check(persistence.sequence == 2 and store.generation == 2);
    try connection.commitTransaction();
    try check(persistence.sequence == 3 and store.generation == 3);
    try connection.beginTransaction();
    try connection.execute("INSERT INTO t VALUES (3, 'rolled back')", &result);
    try connection.rollbackTransaction();
    try connection.execute("SELECT * FROM t ORDER BY id", &result);
    try check(result.row_count == 2 and persistence.sequence == 3);
    const committed_length = try persistence.file.length();
    if (disk.File.open(path)) |duplicate_value| {
        var duplicate = duplicate_value;
        duplicate.close();
        return error.ConcurrentPersistenceWriterAccepted;
    } else |err| try check(err == error.DataDirectoryBusy);
    try persistence.file.appendAll("RAMS");
    try persistence.file.sync();
    persistence.close();
    if (connection.execute("INSERT INTO t VALUES (3, 'closed')", &result)) |_| return error.ClosedPersistenceWriteAccepted else |err| try check(err == error.StorageUnavailable);
    // Recovery repairs only an incomplete tail and restores complete SQL batches.
    store = .{};
    const recovered = try persistence.init(path, &store, &workspace, &recovery_buffer);
    try check(recovered.sequence == 3 and recovered.truncated_tail and try persistence.file.length() == committed_length);
    try connection.execute("SELECT * FROM t ORDER BY id", &result);
    try check(result.row_count == 2 and result.rows[0].cells[0].integer == 1 and result.rows[1].cells[0].integer == 2);
    try check(std.mem.eql(u8, result.rows[0].cells[1].text.slice(), "committed; newline"));
    try connection.execute("INSERT INTO t VALUES (3, 'after restart')", &result);
    try check(persistence.sequence == 4 and store.generation == 4);
    // Actual native I/O failure occurs before publishing the prepared candidate.
    persistence.file.close();
    if (connection.execute("INSERT INTO t VALUES (4, 'must not publish')", &result)) |_| return error.NativeWriteFailureIgnored else |_| try check(store.storage_failed);
    try connection.execute("SELECT * FROM t ORDER BY id", &result);
    try check(result.row_count == 3 and store.generation == 4);
    if (connection.execute("DELETE FROM t", &result)) |_| return error.FailedStorageReused else |err| try check(err == error.StorageUnavailable);
    persistence.close();
    store = .{};
    const reopened = try persistence.init(path, &store, &workspace, &recovery_buffer);
    try check(reopened.sequence == 4 and !store.storage_failed);
    try connection.execute("SELECT * FROM t ORDER BY id", &result);
    try check(result.row_count == 3);
    persistence.close();

    // A valid framed batch with a bad second SQL statement cannot publish its
    // successful first statement, and semantic failure never rewrites the log.
    try clearLog(path);
    var malformed: [512]u8 = undefined;
    var used: usize = 0;
    addFrame(&malformed, &used, "CREATE TABLE doomed (id INTEGER)");
    addFrame(&malformed, &used, "INSERT INTO missing VALUES (1)");
    var file = try disk.File.open(path);
    try log.append(&file, 1, malformed[0..used]);
    const bad_length = try file.length();
    file.close();
    store = .{};
    if (persistence.init(path, &store, &workspace, &recovery_buffer)) |_| return error.BadSemanticReplayAccepted else |_| try check(store.storage_failed and store.commit_fn == null);
    if (store.database.execute("SELECT * FROM doomed", &result)) |_| return error.PartialBatchPublished else |_| try check(true);
    file = try disk.File.open(path);
    try check(try file.length() == bad_length);
    file.close();
    // A syntactically truncated SQL frame inside a checksummed WAL is corruption.
    try clearLog(path);
    file = try disk.File.open(path);
    try log.append(&file, 1, &.{ 100, 0, 0, 0, 'x' });
    file.close();
    store = .{};
    if (persistence.init(path, &store, &workspace, &recovery_buffer)) |_| return error.BadBatchFrameAccepted else |err| try check(err == error.InvalidCommitBatch);
    // The configured recovery capacity is also enforced before append/ack.
    try clearLog(path);
    var small: [8]u8 = undefined;
    store = .{};
    _ = try persistence.init(path, &store, &workspace, &small);
    if (connection.execute("CREATE TABLE denied (id INTEGER)", &result)) |_| return error.UnrecoverableLargeCommitAccepted else |err| try check(err == error.TransactionLogCapacity);
    try check(try persistence.file.length() == 0 and store.generation == 0);
    if (store.database.execute("SELECT * FROM denied", &result)) |_| return error.CapacityFailurePublished else |_| try check(true);
    persistence.close();
    var report_buffer: [160]u8 = undefined;
    const report = try std.fmt.bufPrint(&report_buffer, "{{\"suite\":\"persistence\",\"pass\":true,\"assertions\":{d}}}\n", .{assertions});
    try std.Io.File.stdout().writeStreamingAll(init.io, report);
}
