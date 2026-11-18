//! Product persistence against two manifest-selected native checkpoint generations.
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

fn refuses(layout: persistence.CheckpointLayout, expected_identity: manifest.DatabaseIdentity, reason: anyerror) !void {
    try bind(&staged, 1);
    if (durable.initCheckpoint(layout.stem, expected_identity, &staged, &buffers[1])) |_| return error.CheckpointAccepted else |err| try check(err == reason);
    try check(staged.storage_failed and staged.commit_fn == null and staged.prepare_fn == null);
    try check(staged.database.csn == 0 and staged.database.tableCount() == 0);
    var path_buf: [128]u8 = undefined;
    var contender: disk.WriterLock = .{};
    try contender.acquire(try layout.stablePath(&path_buf, .lock));
    contender.close();
}

fn writeManifest(pathname: []const u8, selection: manifest.Selection) !void {
    var selected = try disk.File.createExclusive(pathname);
    defer selected.close();
    try selection.write(&selected);
}

fn selectManifest(lock_path: []const u8, pending: []const u8, selected: []const u8) !void {
    var lock: disk.WriterLock = .{};
    try lock.acquire(lock_path);
    defer lock.close();
    try disk.replaceAtomic(pending, selected);
}

fn buildGeneration(layout: persistence.CheckpointLayout, generation: u64, database: *Raw, sequence: u64) !manifest.Selection {
    var names: [4][128]u8 = undefined;
    const image_path = try layout.generationPath(&names[0], generation, .image);
    const wal_path = try layout.generationPath(&names[1], generation, .wal);
    const staging_image = try std.fmt.bufPrint(&names[2], "{s}.pending", .{image_path});
    const staging_wal = try std.fmt.bufPrint(&names[3], "{s}.pending", .{wal_path});
    var image = try disk.File.createExclusive(staging_image);
    defer disk.remove(staging_image) catch {};
    defer image.close();
    var envelope = try Writer.begin(&image, .{
        .database_id = identity.database, .generation = generation, .wal_sequence = sequence, .sql_csn = database.csn,
    });
    var scratch: [128]u8 = undefined;
    try Raw.BaseSnapshot.write(database, Sink{ .writer = &envelope }, &scratch);
    const seal = try envelope.finish();
    image.close();

    const selection: manifest.Selection = .{
        .identity = identity, .generation = seal.identity.generation, .base_sequence = seal.identity.wal_sequence,
        .sql_csn = seal.identity.sql_csn, .image_bytes = seal.payload_bytes, .image_digest = seal.digest,
        .anchor_offset = 0, .anchor_length = manifest.record_size, .anchor_kind = persistence.checkpoint_anchor_kind,
    };
    var wal = try disk.File.createExclusive(staging_wal);
    defer disk.remove(staging_wal) catch {};
    defer wal.close();
    const anchor = try selection.encode();
    try log.appendKind(&wal, try selection.anchorSequence(), &anchor, persistence.checkpoint_anchor_kind);
    wal.close();
    try disk.publishNew(staging_image, image_path);
    try disk.publishNew(staging_wal, wal_path);
    return selection;
}

fn assertData(client: *Store.Connection, label: []const u8) !void {
    try client.execute("SELECT cents FROM amounts ORDER BY id", &result);
    try check(result.row_count == 2 and result.rows[0].cells[0].integer == 29 and result.rows[1].cells[0] == .null_value);
    try client.execute("SELECT label FROM notes ORDER BY id", &result);
    try check(result.row_count == 2 and std.mem.eql(u8, result.rows[1].cells[0].text.slice(), label));
}

pub fn main() !void {
    const pid: u32 = if (builtin.os.tag == .windows) Win.GetCurrentProcessId() else if (builtin.os.tag == .macos) @intCast(std.c.getpid()) else @intCast(std.os.linux.getpid());
    var names: [11][128]u8 = undefined;
    const stem = try std.fmt.bufPrint(&names[0], "sig-out/ram-checkpoint-{d}", .{pid});
    const layout = try persistence.CheckpointLayout.init(stem);
    const legacy = try path(&names[1], pid, "source");
    const lock_path = try layout.stablePath(&names[2], .lock);
    const selected_path = try layout.stablePath(&names[3], .manifest);
    const pending_path = try path(&names[4], pid, "pending");
    const first_image = try layout.generationPath(&names[5], 7, .image);
    const first_wal = try layout.generationPath(&names[6], 7, .wal);
    const next_image = try layout.generationPath(&names[7], 8, .image);
    const next_wal = try layout.generationPath(&names[8], 8, .wal);
    const hidden_image = try path(&names[9], pid, "image-hidden");
    const hidden_wal = try path(&names[10], pid, "wal-hidden");
    defer for ([_][]const u8{ legacy, lock_path, selected_path, pending_path, first_image, first_wal, next_image, next_wal, hidden_image, hidden_wal }) |file|
        disk.remove(file) catch {};

    if (persistence.CheckpointLayout.init("")) |_| return error.EmptyStemAccepted else |err| try check(err == error.InvalidPath);
    if (persistence.CheckpointLayout.init("sig-out/")) |_| return error.DirectoryStemAccepted else |err| try check(err == error.InvalidPath);
    var small_path: [2]u8 = undefined;
    if (layout.generationPath(&small_path, 0, .image)) |_| return error.ZeroGenerationAccepted else |err| try check(err == error.InvalidCheckpointManifest);
    if (layout.generationPath(&small_path, 1, .image)) |_| return error.ShortPathAccepted else |err| try check(err == error.PathTooLong);

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
    const first_selection = try buildGeneration(layout, 7, &source.database, source_wal.sequence);
    source_wal.close();
    try writeManifest(pending_path, first_selection);
    try selectManifest(lock_path, pending_path, selected_path);

    var foreign = identity;
    foreign.namespace[0] ^= 1;
    try refuses(layout, foreign, error.CheckpointDatabaseMismatch);

    try bind(&staged, 1);
    const first = try durable.initCheckpoint(stem, identity, &staged, &buffers[1]);
    defer durable.close();
    try check(first.sequence == (try first_selection.anchorSequence()) and !first.truncated_tail);
    try check(staged.database.csn == first_selection.sql_csn and staged.commit_fn != null and staged.prepare_fn == null);
    try bind(&source, 0);
    if (source_wal.initCheckpoint(stem, identity, &source, &buffers[0])) |_| return error.DuplicateWriter else |err| try check(err == error.DataDirectoryBusy);
    var client: Store.Connection = .{ .store = &staged, .id = 2 };
    try client.execute("BEGIN", &result);
    try client.execute("UPDATE amounts SET cents=29 WHERE id=1", &result);
    try client.execute("INSERT INTO notes VALUES (2,'first suffix')", &result);
    try client.execute("COMMIT", &result);
    try check(durable.sequence == first.sequence + 1 and staged.database.csn == first_selection.sql_csn + 1);

    const next_selection = try buildGeneration(layout, 8, &staged.database, durable.sequence);
    try writeManifest(pending_path, next_selection);
    if (selectManifest(lock_path, pending_path, selected_path)) |_| return error.SelectedWhileWriterActive else |err| try check(err == error.DataDirectoryBusy);
    durable.close();
    try check(staged.storage_failed and staged.commit_fn == null);
    if (client.execute("DELETE FROM amounts", &result)) |_| return error.ClosedStoreCommitted else |err| try check(err == error.StorageUnavailable);

    var wal = try disk.File.openExisting(first_wal);
    defer wal.close();
    const valid = try wal.length();
    try wal.appendAll("RAMSQLW1");
    try wal.sync();
    wal.close();
    try bind(&staged, 1);
    const still_first = try durable.initCheckpoint(stem, identity, &staged, &buffers[1]);
    try check(still_first.sequence == first.sequence + 1 and still_first.truncated_tail and still_first.valid_bytes == valid);
    client = .{ .store = &staged, .id = 3 };
    try assertData(&client, "first suffix");
    durable.close();

    // Both generations exist. Changing only the selector moves the next
    // restart to the new files using the same stable stem and lock.
    try selectManifest(lock_path, pending_path, selected_path);
    try disk.publishNew(next_image, hidden_image);
    try refuses(layout, identity, error.FileNotFound);
    try disk.publishNew(hidden_image, next_image);
    try disk.publishNew(next_wal, hidden_wal);
    try refuses(layout, identity, error.FileNotFound);
    var wrong = try disk.File.createExclusive(next_wal);
    defer wrong.close();
    const wrong_anchor = try first_selection.encode();
    const anchor_sequence = try next_selection.anchorSequence();
    try log.appendKind(&wrong, anchor_sequence, &wrong_anchor, persistence.checkpoint_anchor_kind);
    try wrong.appendAll("RAMSQLW1");
    try wrong.sync();
    const wrong_length = try wrong.length();
    wrong.close();
    try refuses(layout, identity, error.CheckpointAnchorMismatch);
    wrong = try disk.File.openExisting(next_wal);
    try check(try wrong.length() == wrong_length);
    wrong.close();
    try disk.remove(next_wal);
    try disk.publishNew(hidden_wal, next_wal);

    try bind(&staged, 1);
    const second = try durable.initCheckpoint(stem, identity, &staged, &buffers[1]);
    try check(second.sequence == anchor_sequence and !second.truncated_tail and staged.database.csn == next_selection.sql_csn);
    client = .{ .store = &staged, .id = 4 };
    try assertData(&client, "first suffix");
    try client.execute("UPDATE notes SET label='second suffix' WHERE id=2", &result);
    try check(durable.sequence == second.sequence + 1 and staged.database.csn == next_selection.sql_csn + 1);
    durable.close();

    try bind(&staged, 1);
    const reopened = try durable.initCheckpoint(stem, identity, &staged, &buffers[1]);
    try check(reopened.sequence == second.sequence + 1 and !reopened.truncated_tail);
    client = .{ .store = &staged, .id = 5 };
    try assertData(&client, "second suffix");
    wal = try disk.File.openExisting(first_wal);
    try check(try wal.length() == valid);
    wal.close();
    std.debug.print("{{\"suite\":\"checkpoint_startup\",\"pass\":true,\"assertions\":{d}}}\n", .{assertions});
}
