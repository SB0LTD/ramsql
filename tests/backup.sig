const std = @import("std");
const builtin = @import("builtin");
const sql = @import("sql");
const stores = @import("db_store");
const disk = @import("db_file");
const backup = @import("backup");
const persistence_module = @import("persistence");
const Raw = sql.Database(.{ .tables = 2, .columns = 3, .rows_per_table = 8, .text_bytes = 32 });
const Store = stores.Store(Raw);
const Persistence = persistence_module.Persistence(Raw, Store);
/// One constant sizes the transaction encode buffer and both recovery buffers.
const transaction_bytes = 4096;
const Win = struct {
    extern "kernel32" fn GetCurrentProcessId() callconv(.winapi) u32;
};
var assertions: usize = 0;
fn check(ok: bool) !void {
    assertions += 1;
    if (!ok) return error.BackupContractFailed;
}
pub fn main(init: std.process.Init) !void {
    const pid: u32 = if (builtin.os.tag == .windows) Win.GetCurrentProcessId() else if (builtin.os.tag == .macos) @intCast(std.c.getpid()) else @intCast(std.os.linux.getpid());
    var source_buffer: [256]u8 = undefined;
    const source_path = try std.fmt.bufPrint(&source_buffer, "sig-out/backup-contract-{d}.wal", .{pid});
    var target_buffer: [256]u8 = undefined;
    const target_path = try std.fmt.bufPrint(&target_buffer, "sig-out/backup-contract-{d}.backup", .{pid});
    var temporary_buffer: [256]u8 = undefined;
    const temporary_path = try std.fmt.bufPrint(&temporary_buffer, "{s}.tmp", .{target_path});
    var created = try disk.File.createExclusive(source_path);
    created.close();
    defer disk.remove(source_path) catch {};
    defer disk.remove(target_path) catch {};
    var state: Persistence = .{};
    defer state.close();
    var store: Store = .{};
    var recovery: [transaction_bytes]u8 = undefined;
    _ = try state.initExisting(source_path, &store, &recovery);
    var connection: Store.Connection = .{ .store = &store, .id = 1 };
    var result: Raw.Result = .{};
    try connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)", &result);
    try connection.execute("INSERT INTO t VALUES (1, 'restorable')", &result);
    // A multi-op kind-3 transaction record is part of the verified backup.
    try connection.execute("BEGIN", &result);
    try connection.execute("INSERT INTO t VALUES (2, 'temporary')", &result);
    try connection.execute("UPDATE t SET value = 'kept' WHERE id = 2", &result);
    try connection.execute("DELETE FROM t WHERE id = 2", &result);
    try connection.execute("INSERT INTO t VALUES (3, 'committed')", &result);
    try connection.execute("COMMIT", &result);
    var scratch: [7]u8 = undefined; // Odd caller buffers exercise all copy/readback edges.
    var empty: [0]u8 = .{};
    if (backup.create(&state.file, target_path, &empty)) |_| return error.SmallScratchAccepted else |err| try check(err == error.BackupBufferCapacity);
    // A pre-existing temporary is preserved, including its contents.
    var temporary = try disk.File.createExclusive(temporary_path);
    try temporary.appendAll("pre-existing artifact");
    try temporary.sync();
    temporary.close();
    if (backup.create(&state.file, target_path, &scratch)) |_| return error.ExistingTemporaryOverwritten else |_| try check(true);
    temporary = try disk.File.openExisting(temporary_path);
    var read_buffer: [128]u8 = undefined;
    const preserved = try temporary.readAt(0, &read_buffer);
    try check(std.mem.eql(u8, read_buffer[0..preserved], "pre-existing artifact"));
    temporary.close();
    try disk.remove(temporary_path);
    try backup.create(&state.file, target_path, &scratch);
    const source_length = try state.file.length();
    var target = try disk.File.openExisting(target_path);
    try check(try target.length() == source_length);
    target.close();
    if (backup.create(&state.file, target_path, &scratch)) |_| return error.ExistingBackupOverwritten else |err| try check(err == error.FileRenameFailed);
    // Failed publication removed only its own new temporary, leaving the old
    // destination unchanged and permitting a future explicit new destination.
    temporary = try disk.File.createExclusive(temporary_path);
    temporary.close();
    try disk.remove(temporary_path);
    try check(try state.file.length() == source_length);
    var restored: Persistence = .{};
    defer restored.close();
    var restored_store: Store = .{};
    var restored_recovery: [transaction_bytes]u8 = undefined;
    const restored_result = try restored.initExisting(target_path, &restored_store, &restored_recovery);
    try check(restored_result.sequence == 3);
    var restored_connection: Store.Connection = .{ .store = &restored_store, .id = 2 };
    try restored_connection.execute("SELECT * FROM t", &result);
    try check(result.row_count == 2 and std.mem.eql(u8, result.rows[0].cells[1].text.slice(), "restorable"));
    try check(result.rows[1].cells[0].integer == 3 and std.mem.eql(u8, result.rows[1].cells[1].text.slice(), "committed"));
    restored.close();
    // A missing offline source fails without creating an empty database.
    var missing: Persistence = .{};
    var missing_store: Store = .{};
    if (missing.initExisting(temporary_path, &missing_store, &recovery)) |_| return error.MissingSourceCreated else |err| try check(err == error.FileOpenFailed);
    temporary = try disk.File.createExclusive(temporary_path);
    try check(try temporary.length() == 0);
    temporary.close();
    try disk.remove(temporary_path);
    var report_buffer: [160]u8 = undefined;
    const report = try std.fmt.bufPrint(&report_buffer, "{{\"suite\":\"backup\",\"pass\":true,\"assertions\":{d}}}\n", .{assertions});
    try std.Io.File.stdout().writeStreamingAll(init.io, report);
}
