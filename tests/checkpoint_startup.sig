//! Product persistence against actual native checkpoint files.
const std = @import("std");
const builtin = @import("builtin");
const sql = @import("sql");
const disk = @import("db_file");
const log = @import("durable_log");
const snapshot = @import("snapshot_envelope");
const manifest = @import("checkpoint_manifest");
const persistence = @import("persistence");

const Raw = sql.Database(.{
    .tables = 2, .columns = 3, .rows_per_table = 8, .text_bytes = 64,
    .external_storage = true, .split_external_versions = true, .integral_external_versions = true,
    .work_rows = 64, .result_rows = 16, .integer_bits = 32, .tx_writes = 32,
});
const Store = @import("db_store").Store(Raw);
const Persistence = persistence.Persistence(Raw, Store);
const budgets: [2]Raw.TableBudget = .{
    .{ .rows = 8, .versions = 16, .integral_row_words = 2 },
    .{ .rows = 8, .versions = 16 },
};
const Writer = snapshot.Writer(disk.File);
const Sink = struct {
    writer: *Writer,
    pub fn appendAll(self: Sink, bytes: []const u8) !void { try self.writer.append(bytes); }
};
const Win = struct {
    extern "kernel32" fn GetCurrentProcessId() callconv(.winapi) u32;
};
const identity: manifest.DatabaseIdentity = .{ .database = "database-id-1234".*, .namespace = "namespace-id-123".*, .oid = 16384 };
var source: Store = .{};
var staged: Store = .{};
var source_wal: Persistence = .{};
var durable: Persistence = .{};
var backing: [2][131072]u8 align(64) = undefined;
var buffers: [2][4096]u8 = undefined;
var result: Raw.Result = .{};
var assertions: usize = 0;

fn check(ok: bool) !void {
    assertions += 1;
    if (!ok) {
        std.debug.print("checkpoint startup assertion {d}\n", .{assertions});
        return error.CheckpointStartupContract;
    }
}

fn bind(store: *Store, index: usize) !void {
    store.* = .{};
    store.database_oid = identity.oid;
    const refs = 4096;
    const needed = try Raw.backingBytesFor(budgets, refs);
    try store.database.bindMemoryFor(backing[index][0..needed], budgets, refs);
}

fn path(bytes: []u8, pid: u32, suffix: []const u8) ![]const u8 {
    return std.fmt.bufPrint(bytes, "sig-out/ram-checkpoint-{d}.{s}", .{ pid, suffix });
}

fn refuses(paths: persistence.CheckpointPaths, expected_identity: manifest.DatabaseIdentity, reason: anyerror) !void {
    try bind(&staged, 1);
    if (durable.initCheckpoint(paths, expected_identity, &staged, &buffers[1])) |_| return error.CheckpointAccepted else |err| try check(err == reason);
    try check(staged.storage_failed and staged.commit_fn == null and staged.prepare_fn == null);
    var contender: disk.WriterLock = .{};
    try contender.acquire(paths.writer_lock);
    contender.close();
}

pub fn main() !void {
    const pid: u32 = if (builtin.os.tag == .windows) Win.GetCurrentProcessId() else if (builtin.os.tag == .macos) @intCast(std.c.getpid()) else @intCast(std.os.linux.getpid());
    var locations: [7][128]u8 = undefined;
    const legacy = try path(&locations[0], pid, "source");
    const paths: persistence.CheckpointPaths = .{
        .writer_lock = try path(&locations[1], pid, "lock"), .selection = try path(&locations[2], pid, "manifest"),
        .image = try path(&locations[3], pid, "image"), .wal = try path(&locations[4], pid, "wal"),
    };
    const invalid_wal = try path(&locations[5], pid, "bad");
    const empty_image = try path(&locations[6], pid, "absent");
    defer for ([_][]const u8{ legacy, paths.writer_lock, paths.selection, paths.image, paths.wal, invalid_wal }) |file|
        disk.remove(file) catch {};

    var initial_file = try disk.File.createExclusive(legacy);
    initial_file.close();
    try bind(&source, 0);
    _ = try source_wal.initExisting(legacy, &source, &buffers[0]);
    defer source_wal.close();
    var writer: Store.Connection = .{ .store = &source, .id = 1 };
    try writer.execute("CREATE TABLE amounts (id INT PRIMARY KEY, cents BIGINT)", &result);
    try writer.execute("CREATE TABLE notes (id INT PRIMARY KEY, label TEXT)", &result);
    try writer.execute("INSERT INTO amounts VALUES (1,20),(2,NULL)", &result);
    try writer.execute("INSERT INTO notes VALUES (1,'held')", &result);
    var image = try disk.File.createExclusive(paths.image);
    defer image.close();
    var envelope = try Writer.begin(&image, .{
        .database_id = identity.database, .generation = 7, .wal_sequence = source_wal.sequence, .sql_csn = source.database.csn,
    });
    var scratch: [128]u8 = undefined;
    try Raw.BaseSnapshot.write(&source.database, Sink{ .writer = &envelope }, &scratch);
    const seal = try envelope.finish();
    image.close();

    const selection: manifest.Selection = .{
        .identity = identity, .generation = seal.identity.generation, .base_sequence = seal.identity.wal_sequence,
        .sql_csn = seal.identity.sql_csn, .image_bytes = seal.payload_bytes, .image_digest = seal.digest,
        .anchor_offset = 0, .anchor_length = manifest.record_size, .anchor_kind = persistence.checkpoint_anchor_kind,
    };
    var selected = try disk.File.createExclusive(paths.selection);
    defer selected.close();
    try selection.write(&selected);
    selected.close();
    var wal = try disk.File.createExclusive(paths.wal);
    defer wal.close();
    const anchor_sequence = try selection.anchorSequence();
    const published = try selection.encode();
    try log.appendKind(&wal, anchor_sequence, &published, persistence.checkpoint_anchor_kind);
    wal.close();

    var foreign = identity;
    foreign.namespace[0] ^= 1;
    try refuses(paths, foreign, error.CheckpointDatabaseMismatch);
    var missing = paths;
    missing.image = empty_image;
    try refuses(missing, identity, error.FileNotFound);

    var bad = try disk.File.createExclusive(invalid_wal);
    defer bad.close();
    var substituted = selection;
    substituted.generation += 1;
    const wrong_anchor = try substituted.encode();
    try log.appendKind(&bad, anchor_sequence, &wrong_anchor, persistence.checkpoint_anchor_kind);
    try bad.appendAll("RAMSQLW1");
    try bad.sync();
    const bad_len = try bad.length();
    bad.close();
    var untrusted = paths;
    untrusted.wal = invalid_wal;
    try refuses(untrusted, identity, error.CheckpointAnchorMismatch);
    bad = try disk.File.openExisting(invalid_wal);
    try check(try bad.length() == bad_len);
    bad.close();

    try bind(&staged, 1);
    const first = try durable.initCheckpoint(paths, identity, &staged, &buffers[1]);
    defer durable.close();
    try check(first.sequence == anchor_sequence and !first.truncated_tail);
    try check(staged.database.csn == selection.sql_csn and staged.commit_fn != null and staged.prepare_fn == null);
    var contender: disk.WriterLock = .{};
    if (contender.acquire(paths.writer_lock)) |_| return error.DuplicateWriter else |err| try check(err == error.DataDirectoryBusy);
    var client: Store.Connection = .{ .store = &staged, .id = 2 };
    try client.execute("BEGIN", &result);
    try client.execute("UPDATE amounts SET cents=29 WHERE id=1", &result);
    try client.execute("INSERT INTO notes VALUES (2,'suffix')", &result);
    try client.execute("COMMIT", &result);
    try check(durable.sequence == first.sequence + 1 and staged.database.csn == selection.sql_csn + 1);
    durable.close();
    try check(staged.storage_failed and staged.commit_fn == null);
    if (client.execute("DELETE FROM amounts", &result)) |_| return error.ClosedStoreCommitted else |err| try check(err == error.StorageUnavailable);

    wal = try disk.File.openExisting(paths.wal);
    const valid = try wal.length();
    try wal.appendAll("RAMSQLW1");
    try wal.sync();
    wal.close();
    try bind(&staged, 1);
    const second = try durable.initCheckpoint(paths, identity, &staged, &buffers[1]);
    try check(second.sequence == first.sequence + 1 and second.truncated_tail and second.valid_bytes == valid);
    client = .{ .store = &staged, .id = 3 };
    try client.execute("SELECT cents FROM amounts ORDER BY id", &result);
    try check(result.row_count == 2 and result.rows[0].cells[0].integer == 29 and result.rows[1].cells[0] == .null_value);
    try client.execute("SELECT label FROM notes ORDER BY id", &result);
    try check(result.row_count == 2 and std.mem.eql(u8, result.rows[1].cells[0].text.slice(), "suffix"));
    std.debug.print("{{\"suite\":\"checkpoint_startup\",\"pass\":true,\"assertions\":{d}}}\n", .{assertions});
}
