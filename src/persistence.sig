//! Product-owned durable SQL replay and store commit policy.
//! ZPM owns portable framing, change records and native I/O. The product keeps
//! a single active WAL and publishes commits only after stable storage.
//! Checkpoint mode retains its stable writer lock while replacing the manifest
//! and switching the active WAL. Ordinary WAL mode keeps its original file.
//!
//! Payload kinds: 3 is the committed change record, 4 is two-phase state. Legacy
//! SQL-text kinds are replayed with the alpha grammar only: kind 2 for
//! PostgreSQL 32-bit INTEGER configurations, kind 1 for 64-bit ones. Exactly one
//! legacy kind is accepted per configuration, and a legacy record after any
//! versioned record fails closed. Every legacy record replays in one MVCC
//! transaction that publishes only when all its statements succeed; no
//! database copy is used.
const disk = @import("db_file");
const log = @import("durable_log");
const batches = @import("db_store");
const snapshot = @import("snapshot_envelope");
const manifest = @import("checkpoint_manifest");
const std = @import("std");

pub const change_kind: u16 = 3;
const database_identity_kind: u16 = 8;
pub const checkpoint_anchor_kind: u16 = 24;
pub const CheckpointIdentity = manifest.DatabaseIdentity;
pub const CheckpointPhase = enum { staged, before_selection, after_selection, after_handoff };
pub const CheckpointObserver = *const fn (CheckpointPhase) void;
pub const DatabaseIdentity = struct { cluster: [16]u8, oid: u32 };

fn databaseIdentityPayload(identity: DatabaseIdentity) [20]u8 {
    var payload: [20]u8 = undefined;
    payload[0..16].* = identity.cluster;
    std.mem.writeInt(u32, payload[16..20], identity.oid, .little);
    return payload;
}

fn appendDatabaseIdentity(file: *disk.File, identity: DatabaseIdentity) !void {
    const payload = databaseIdentityPayload(identity);
    try log.appendKind(file, 1, &payload, database_identity_kind);
}

/// Publish only a complete, synced identity. A previously published file
/// can be reused before the catalog marker only when it is exactly empty.
pub fn publishEmptyDatabase(path: []const u8, temporary: []const u8, identity: DatabaseIdentity) !void {
    if (disk.File.openExisting(path)) |opened| {
        var file = opened;
        defer file.close();
        const expected = databaseIdentityPayload(identity);
        if (try file.length() != log.overhead + expected.len) return error.InvalidDatabaseIdentity;
        var scratch: [expected.len]u8 = undefined;
        const payload = try log.readFrame(&file, &scratch, .{
            .offset = 0, .sequence = 1, .kind = database_identity_kind, .payload_len = expected.len,
        });
        if (!std.mem.eql(u8, payload, &expected)) return error.InvalidDatabaseIdentity;
        return;
    } else |err| {
        if (err != error.FileNotFound) return err;
    }
    var file = try disk.File.createExclusive(temporary);
    defer disk.remove(temporary) catch {};
    defer file.close();
    try appendDatabaseIdentity(&file, identity);
    file.close();
    try disk.publishNew(temporary, path);
}

/// One configured stem owns the fixed writer lock and selector paths. Only the
/// selection read under that lock supplies a generation for its image and WAL.
pub const CheckpointLayout = struct {
    pub const path_capacity = 4096;
    const max_suffix = ".g18446744073709551615.manifest.pending".len;
    pub const Stable = enum { lock, manifest };
    pub const Generation = enum { image, wal };

    stem: []const u8,

    pub fn init(stem: []const u8) !CheckpointLayout {
        if (stem.len == 0 or stem[stem.len - 1] == '/' or stem[stem.len - 1] == '\\') return error.InvalidPath;
        if (stem.len > path_capacity - 1 - max_suffix) return error.PathTooLong;
        for (stem) |byte| if (byte == 0) return error.InvalidPath;
        return .{ .stem = stem };
    }

    pub fn stablePath(self: CheckpointLayout, out: []u8, kind: Stable) ![]const u8 {
        return std.fmt.bufPrint(out, "{s}.{s}", .{ self.stem, @tagName(kind) }) catch return error.PathTooLong;
    }

    pub fn generationPath(self: CheckpointLayout, out: []u8, generation: u64, kind: Generation) ![]const u8 {
        if (generation == 0) return error.InvalidCheckpointManifest;
        return std.fmt.bufPrint(out, "{s}.g{d}.{s}", .{ self.stem, generation, @tagName(kind) }) catch return error.PathTooLong;
    }
};

/// Stages an immutable sealed image and its initial WAL. The caller must
/// freeze the source database and hold its writer lock while sampling the
/// sequence and writing the image. This function never selects the generation;
/// the caller can publish its manifest only after coordinating the WAL cut.
pub fn stageCheckpointGeneration(
    comptime Raw: type,
    layout: CheckpointLayout,
    generation: u64,
    identity: manifest.DatabaseIdentity,
    database: *const Raw,
    sequence: u64,
    scratch: []u8,
) !manifest.Selection {
    if (identity.oid == 0 or generation == 0 or sequence == std.math.maxInt(u64))
        return error.InvalidCheckpointManifest;
    const Writer = snapshot.Writer(disk.File);
    const Sink = struct {
        writer: *Writer,
        pub fn appendAll(self: @This(), bytes: []const u8) !void {
            try self.writer.append(bytes);
        }
    };
    var names: [4][CheckpointLayout.path_capacity]u8 = undefined;
    const image_path = try layout.generationPath(&names[0], generation, .image);
    const wal_path = try layout.generationPath(&names[1], generation, .wal);
    const pending_image = std.fmt.bufPrint(&names[2], "{s}.pending", .{image_path}) catch return error.PathTooLong;
    const pending_wal = std.fmt.bufPrint(&names[3], "{s}.pending", .{wal_path}) catch return error.PathTooLong;

    var image = try disk.File.createExclusive(pending_image);
    defer disk.remove(pending_image) catch {};
    defer image.close();
    var writer = try Writer.begin(&image, .{
        .database_id = identity.database, .generation = generation,
        .wal_sequence = sequence, .sql_csn = database.csn,
    });
    try Raw.BaseSnapshot.write(database, Sink{ .writer = &writer }, scratch);
    const seal = try writer.finish();
    image.close();

    const selection: manifest.Selection = .{
        .identity = identity, .generation = generation, .base_sequence = sequence,
        .sql_csn = database.csn, .image_bytes = seal.payload_bytes, .image_digest = seal.digest,
        .anchor_offset = 0, .anchor_length = manifest.record_size, .anchor_kind = checkpoint_anchor_kind,
    };
    const anchor = try selection.encode();
    var wal = try disk.File.createExclusive(pending_wal);
    defer disk.remove(pending_wal) catch {};
    defer wal.close();
    try log.appendKind(&wal, try selection.anchorSequence(), &anchor, checkpoint_anchor_kind);
    wal.close();

    // A later error may leave an unselected image. Never replace an existing
    // file or remove a final pathname that another operation could own.
    try disk.publishNew(pending_image, image_path);
    try disk.publishNew(pending_wal, wal_path);
    return selection;
}

fn temporaryManifestPath(layout: CheckpointLayout, generation: u64, out: []u8) ![]const u8 {
    return std.fmt.bufPrint(out, "{s}.g{d}.manifest.pending", .{ layout.stem, generation }) catch return error.PathTooLong;
}

fn pathOccupied(path: []const u8) !bool {
    var file = disk.File.openExisting(path) catch |err| switch (err) {
        error.FileNotFound => return false,
        error.DataDirectoryBusy => return true,
        else => return err,
    };
    file.close();
    return true;
}

fn nextGeneration(layout: CheckpointLayout, active: u64) !u64 {
    var buffer: [CheckpointLayout.path_capacity]u8 = undefined;
    var path_buffer: [CheckpointLayout.path_capacity]u8 = undefined;
    var number = active;
    // A killed process may leave an unselected file. Skip it without modifying
    // it, with a bounded lookup even when old files were never collected.
    for (0..64) |_| {
        number = std.math.add(u64, number, 1) catch return error.GenerationExhausted;
        if (try pathOccupied(try temporaryManifestPath(layout, number, &buffer))) continue;
        for ([_]CheckpointLayout.Generation{ .image, .wal }) |kind| {
            const path = try layout.generationPath(&path_buffer, number, kind);
            if (try pathOccupied(path)) break;
            const pending = std.fmt.bufPrint(&buffer, "{s}.pending", .{path}) catch return error.PathTooLong;
            if (try pathOccupied(pending)) break;
        } else return number;
    }
    return error.CheckpointGenerationCapacity;
}

const ImagePayload = struct {
    file: *disk.File,
    bytes: u64,

    pub fn length(self: ImagePayload) !u64 {
        return self.bytes;
    }

    pub fn readAt(self: ImagePayload, offset: u64, out: []u8) !usize {
        if (offset >= self.bytes) return 0;
        const count = @min(out.len, self.bytes - offset);
        return self.file.readAt(snapshot.header_size + offset, out[0..count]);
    }
};

pub fn Persistence(comptime Raw: type, comptime Store: type) type {
    return struct {
        const Self = @This();
        const OpenMode = enum { open_or_create, existing, exclusive };
        const prepare_kind = Raw.PreparedWal.wal_kind;
        const legacy_kind: u16 = if (@hasDecl(Raw, "integer_type") and Raw.integer_type == .integer32) 2 else 1;
        pub const accepted_kinds: u32 = (@as(u32, 1) << legacy_kind) | (@as(u32, 1) << change_kind) | (@as(u32, 1) << prepare_kind);
        file: disk.File = .{},
        writer_lock: disk.WriterLock = .{},
        store: ?*Store = null,
        replay_result: Raw.Result = .{},
        legacy_rows: [if (Raw.Result.external_rows) 1 else 0]Raw.Result.ResultRow = undefined,
        /// Bounded overlay that validates each whole change record first.
        replay_scratch: Raw.ReplayScratch = .{},
        recovery_payload: []u8 = &.{},
        pending_frames: [Store.prepared_capacity]?log.Frame = @splat(null),
        /// WAL sequence of the last durable record (the commit order).
        sequence: u64 = 0,
        max_batch_bytes: usize = 0,
        seen_change_record: bool = false,
        active: bool = false,
        expected_identity: ?DatabaseIdentity = null,
        seen_identity: bool = false,
        checkpoint_layout: ?CheckpointLayout = null,
        checkpoint_selection: ?manifest.Selection = null,

        /// self and all caller storage must retain stable addresses until close.
        /// store must be newly initialized. recovery_buffer must fit the
        /// largest permitted committed transaction; after recovery it becomes
        /// the store's commit encode buffer, so every record the server writes
        /// fits on recovery. Call before binding listeners; any recovery error
        /// aborts startup.
        pub fn init(self: *Self, path: []const u8, store: *Store, recovery_buffer: []u8) !log.Recovery {
            return self.initImpl(path, store, recovery_buffer, .open_or_create, null);
        }

        /// Offline maintenance requires a real existing source; a misspelled
        /// database path must never silently become an empty valid backup.
        pub fn initExisting(self: *Self, path: []const u8, store: *Store, recovery_buffer: []u8) !log.Recovery {
            return self.initImpl(path, store, recovery_buffer, .existing, null);
        }

        pub fn initDatabase(self: *Self, path: []const u8, store: *Store, recovery_buffer: []u8, identity: DatabaseIdentity, create: bool) !log.Recovery {
            return self.initImpl(path, store, recovery_buffer, if (create) .exclusive else .existing, identity);
        }

        /// Called under cluster ownership with no other source sessions. The
        /// reserved destination path becomes eligible for catalog publication
        /// only after the new WAL is synced and replayed into an empty store.
        pub fn forkDatabase(self: *Self, path: []const u8, identity: DatabaseIdentity, scratch: []u8) !void {
            const source = self.expected_identity orelse return error.InvalidDatabaseIdentity;
            if (identity.oid == 0 or source.oid == identity.oid or !std.mem.eql(u8, &source.cluster, &identity.cluster))
                return error.InvalidDatabaseIdentity;
            const live = self.store orelse return error.PersistenceClosed;
            if (!self.active or self.file.poisoned or live.storage_failed) return error.StorageUnavailable;
            if (live.preparedCount() != 0) return error.DatabaseBusy;
            var file = try disk.File.createExclusive(path);
            defer file.close();
            const kinds = accepted_kinds | (@as(u32, 1) << database_identity_kind);
            var identities: ForkIdentities = .{ .source = source, .destination = identity.oid };
            try log.cloneFrames(&self.file, &file, scratch, self.sequence, kinds, &identities, ForkIdentities.rewrite);
        }

        const ForkIdentities = struct {
            source: DatabaseIdentity,
            destination: u32,

            fn rewrite(self: *ForkIdentities, frame: log.Frame, payload: []u8) !void {
                if (frame.sequence == 1) {
                    if (frame.kind != database_identity_kind or payload.len != 20 or
                        !std.mem.eql(u8, payload[0..16], &self.source.cluster) or
                        std.mem.readInt(u32, payload[16..20], .little) != self.source.oid)
                        return error.InvalidDatabaseIdentity;
                    std.mem.writeInt(u32, payload[16..20], self.destination, .little);
                } else if (frame.kind == database_identity_kind) {
                    return error.InvalidDatabaseIdentity;
                } else if (frame.kind == prepare_kind) {
                    try Raw.PreparedWal.rebindDatabase(payload, self.source.oid, self.destination);
                }
            }
        };

        fn requireFresh(self: *Self, store: *Store, recovery_buffer: []u8) !void {
            if (self.active or self.store != null or store.commit_fn != null or store.prepare_fn != null) return error.PersistenceAlreadyOpen;
            if (store.database.csn != 0 or store.database.tableCount() != 0 or store.preparedCount() != 0) return error.PersistenceRequiresEmptyStore;
            if (recovery_buffer.len == 0) return error.InvalidWorkspace;
        }

        fn initImpl(self: *Self, path: []const u8, store: *Store, recovery_buffer: []u8, mode: OpenMode, identity: ?DatabaseIdentity) !log.Recovery {
            try self.requireFresh(store, recovery_buffer);
            if (comptime Raw.Result.external_rows) try self.replay_result.bindMemory(std.mem.asBytes(&self.legacy_rows), 1);
            store.durability_required = true;
            store.storage_failed = true;
            self.file = switch (mode) {
                .open_or_create => try disk.File.open(path),
                .existing => try disk.File.openExisting(path),
                .exclusive => try disk.File.createExclusive(path),
            };
            self.store = store;
            self.recovery_payload = recovery_buffer;
            self.pending_frames = @splat(null);
            self.max_batch_bytes = recovery_buffer.len;
            self.seen_change_record = false;
            self.expected_identity = identity;
            self.seen_identity = false;
            self.sequence = 0;
            errdefer {
                self.file.close();
                self.store = null;
                self.recovery_payload = &.{};
            }
            if (mode == .exclusive) {
                const created = identity orelse return error.InvalidDatabaseIdentity;
                try appendDatabaseIdentity(&self.file, created);
            }
            const kinds = accepted_kinds | if (identity != null) @as(u32, 1) << database_identity_kind else @as(u32, 0);
            const recovery = try log.recoverFramesWithFinish(&self.file, recovery_buffer, self, Self.replay, Self.finishRecovery, kinds);
            self.attach(store, recovery_buffer, recovery, true);
            return recovery;
        }

        const CheckpointReplay = struct {
            persistence: *Self,
            anchor: [manifest.record_size]u8,
            image: *disk.File,
            seal: snapshot.Seal,
            scratch: []u8,

            fn verify(self: *CheckpointReplay, _: log.Frame, payload: []const u8) !void {
                if (!std.mem.eql(u8, payload, &self.anchor)) return error.CheckpointAnchorMismatch;
                // The borrowed anchor payload is consumed before reusing scratch
                // to verify and restore the image. WAL replay has not started.
                const staged = self.persistence.store orelse return error.PersistenceClosed;
                try snapshot.verify(self.image, self.scratch, self.seal);
                const source: ImagePayload = .{ .file = self.image, .bytes = self.seal.payload_bytes };
                try Raw.BaseSnapshot.restore(&staged.database, source, self.seal.identity.sql_csn, self.scratch, &self.persistence.replay_scratch);
            }

            fn apply(self: *CheckpointReplay, frame: log.Frame, payload: []const u8) !void {
                return self.persistence.replay(frame, payload);
            }
        };

        /// Open the manifest-selected generation under the same stable writer
        /// lock on every restart. The caller supplies trusted configuration and
        /// a fresh unpublished store, which it discards on any failure.
        pub fn initCheckpoint(self: *Self, stem: []const u8, identity: manifest.DatabaseIdentity, store: *Store, recovery_buffer: []u8) !log.Recovery {
            try self.requireFresh(store, recovery_buffer);
            if (store.database_oid != identity.oid or store.prepared_limit != 0) return error.InvalidCheckpointStore;
            if (recovery_buffer.len < manifest.record_size) return error.InvalidWorkspace;
            const layout = try CheckpointLayout.init(stem);
            store.durability_required = true;
            store.storage_failed = true;
            var path: [CheckpointLayout.path_capacity]u8 = undefined;
            try self.writer_lock.acquireExisting(try layout.stablePath(&path, .lock));
            errdefer self.writer_lock.close();
            const selection = selected: {
                var file = try disk.File.openExisting(try layout.stablePath(&path, .manifest));
                defer file.close();
                break :selected try manifest.Selection.read(&file, identity);
            };
            if (selection.anchor_offset != 0 or selection.anchor_kind != checkpoint_anchor_kind or
                selection.anchor_length != manifest.record_size) return error.InvalidCheckpointAnchor;
            var image = try disk.File.openExisting(try layout.generationPath(&path, selection.generation, .image));
            defer image.close();
            self.file = try disk.File.openExisting(try layout.generationPath(&path, selection.generation, .wal));
            errdefer self.file.close();
            self.store = store;
            errdefer self.store = null;
            self.max_batch_bytes = recovery_buffer.len;
            self.expected_identity = null;
            self.seen_identity = false;
            self.seen_change_record = true;
            var state: CheckpointReplay = .{
                .persistence = self,
                .anchor = try selection.encode(),
                .image = &image,
                .seal = .{
                    .identity = .{
                        .database_id = identity.database, .generation = selection.generation,
                        .wal_sequence = selection.base_sequence, .sql_csn = selection.sql_csn,
                    },
                    .payload_bytes = selection.image_bytes, .digest = selection.image_digest,
                },
                .scratch = recovery_buffer,
            };
            const anchor: log.Frame = .{
                .offset = selection.anchor_offset, .sequence = try selection.anchorSequence(),
                .kind = selection.anchor_kind, .payload_len = selection.anchor_length,
            };
            const recovery = try log.recoverAfterFrame(&self.file, recovery_buffer, anchor, &state, CheckpointReplay.verify, CheckpointReplay.apply, null, @as(u32, 1) << change_kind);
            self.checkpoint_layout = layout;
            self.checkpoint_selection = selection;
            self.attach(store, recovery_buffer, recovery, false);
            return recovery;
        }

        /// Take a checkpoint copy of an ordinary WAL while its handle is held
        /// exclusively. Used offline before serving from the new stem.
        pub fn bootstrapCheckpoint(self: *Self, stem: []const u8, identity: CheckpointIdentity, scratch: []u8) !void {
            const store = self.store orelse return error.PersistenceClosed;
            if (!self.active or self.file.poisoned or store.storage_failed or self.checkpoint_selection != null or
                store.database_oid != identity.oid or store.prepared_limit != 0 or store.preparedCount() != 0)
                return error.InvalidCheckpointStore;
            const layout = try CheckpointLayout.init(stem);
            var lock_buffer: [CheckpointLayout.path_capacity]u8 = undefined;
            var manifest_buffer: [CheckpointLayout.path_capacity]u8 = undefined;
            var pending_buffer: [CheckpointLayout.path_capacity]u8 = undefined;
            const lock_path = try layout.stablePath(&lock_buffer, .lock);
            const target = try layout.stablePath(&manifest_buffer, .manifest);
            const pending = try temporaryManifestPath(layout, 1, &pending_buffer);
            if (try pathOccupied(target)) return error.CheckpointAlreadyExists;
            var lock = try disk.File.createExclusive(lock_path);
            defer lock.close();
            try lock.sync();
            const selected = try stageCheckpointGeneration(Raw, layout, 1, identity, &store.database, self.sequence, scratch);
            var file = try disk.File.createExclusive(pending);
            defer disk.remove(pending) catch {};
            defer file.close();
            try selected.write(&file);
            file.close();
            try disk.publishNew(pending, target);
        }

        /// Switch an already running checkpoint database to a fresh WAL. The
        /// caller must hold the same server write gate as commits and prevent
        /// concurrent reads of the database until the method returns.
        pub fn rotateCheckpoint(self: *Self, scratch: []u8, observer: ?CheckpointObserver) !void {
            const layout = self.checkpoint_layout orelse return error.InvalidCheckpointStore;
            const previous = self.checkpoint_selection orelse return error.InvalidCheckpointStore;
            const store = self.store orelse return error.PersistenceClosed;
            if (!self.active or self.file.poisoned or store.storage_failed or store.prepare_fn != null)
                return error.StorageUnavailable;
            if (self.sequence >= std.math.maxInt(u64) - 1) return error.GenerationExhausted;
            if (store.database.csn < previous.sql_csn) return error.InvalidCheckpointStore;
            // Sequence reservations can add WAL records without publishing
            // a row/catalog transaction or advancing the SQL snapshot clock.
            if (self.sequence <= try previous.anchorSequence()) return error.CheckpointNoChanges;
            const generation = try nextGeneration(layout, previous.generation);
            var manifest_buffer: [CheckpointLayout.path_capacity]u8 = undefined;
            var pending_buffer: [CheckpointLayout.path_capacity]u8 = undefined;
            var wal_buffer: [CheckpointLayout.path_capacity]u8 = undefined;
            const target = try layout.stablePath(&manifest_buffer, .manifest);
            const pending = try temporaryManifestPath(layout, generation, &pending_buffer);
            const wal_path = try layout.generationPath(&wal_buffer, generation, .wal);

            const selected = try stageCheckpointGeneration(Raw, layout, generation, previous.identity, &store.database, self.sequence, scratch);
            if (observer) |watch| watch(.staged);
            var next_wal = try disk.File.openExisting(wal_path);
            errdefer next_wal.close();
            const next_sequence = try selected.anchorSequence();
            var file = try disk.File.createExclusive(pending);
            defer disk.remove(pending) catch {};
            defer file.close();
            try selected.write(&file);
            file.close();
            if (observer) |watch| watch(.before_selection);
            disk.replaceAtomic(pending, target) catch |err| {
                // The rename may have succeeded before sync failed. Appending
                // to either WAL could lose acknowledgements after recovery.
                store.storage_failed = true;
                self.file.poison();
                return err;
            };
            if (observer) |watch| watch(.after_selection);

            // From this point the new generation is authoritative. No
            // fallible work may separate selector publication from handoff.
            var old_wal = self.file;
            self.file = next_wal;
            self.sequence = next_sequence;
            self.checkpoint_selection = selected;
            old_wal.close();
            if (observer) |watch| watch(.after_handoff);
        }

        pub fn checkpointBaseSequence(self: *const Self) ?u64 {
            const selected = self.checkpoint_selection orelse return null;
            return selected.anchorSequence() catch unreachable;
        }

        fn attach(self: *Self, store: *Store, recovery_buffer: []u8, recovery: log.Recovery, allow_prepare: bool) void {
            self.sequence = recovery.sequence;
            self.recovery_payload = &.{};
            store.encode_buffer = recovery_buffer;
            store.commit_ctx = self;
            store.commit_fn = &Self.commit;
            store.prepare_fn = if (allow_prepare) &Self.commitPrepared else null;
            store.storage_failed = false;
            self.active = true;
        }

        fn replay(self: *Self, frame: log.Frame, payload: []const u8) !void {
            const store = self.store orelse return error.PersistenceClosed;
            if (frame.kind == database_identity_kind) {
                const expected = self.expected_identity orelse return error.InvalidDatabaseIdentity;
                if (self.seen_identity or frame.sequence != 1 or payload.len != 20 or
                    !std.mem.eql(u8, payload[0..16], &expected.cluster) or
                    std.mem.readInt(u32, payload[16..20], .little) != expected.oid) return error.InvalidDatabaseIdentity;
                self.seen_identity = true;
                return;
            }
            if (self.expected_identity != null and !self.seen_identity) return error.InvalidDatabaseIdentity;
            if (frame.kind == change_kind) {
                // Validated as a whole before anything applies; no copy needed.
                try store.database.applyChangeRecord(payload, &self.replay_scratch);
                self.seen_change_record = true;
                self.sequence = frame.sequence;
                return;
            }
            if (frame.kind == prepare_kind) {
                try self.replayPrepared(store, frame, payload);
                self.seen_change_record = true;
                self.sequence = frame.sequence;
                return;
            }
            if (frame.kind != legacy_kind or self.seen_change_record) return error.UnsupportedLogVersion;
            // Check the complete length framing BEFORE applying anything;
            // malformed tails must not publish even their first valid statement.
            var iterator: batches.BatchIterator = .{ .bytes = payload };
            if ((try iterator.next()) == null) return error.InvalidCommitBatch;
            while (try iterator.next()) |_| {}
            // One MVCC transaction per record: it publishes only when every
            // statement succeeded; any error aborts all of them.
            const database = &store.database;
            const slot = try database.begin();
            errdefer database.abort(slot);
            iterator = .{ .bytes = payload };
            while (try iterator.next()) |statement| {
                try database.statement(slot, statement, &self.replay_result, .{ .mode = .legacy_alpha });
            }
            database.publishCommit(slot);
            self.sequence = frame.sequence;
        }

        fn replayPrepared(self: *Self, store: *Store, frame: log.Frame, payload: []const u8) !void {
            switch (try Raw.PreparedWal.decode(payload)) {
                .prepare => |prepared| {
                    const index = try store.rememberPrepared(prepared, frame.sequence);
                    self.pending_frames[index] = frame;
                },
                .decision => |decision| {
                    const index = try store.recoveredDecisionIndex(decision);
                    // The decision is decoded by value before reusing scratch.
                    if (decision.commit) try self.restorePending(store, index);
                    try store.resolveRecoveredPrepared(index, decision.commit);
                    self.pending_frames[index] = null;
                },
            }
        }

        fn restorePending(self: *Self, store: *Store, index: usize) !void {
            const frame = self.pending_frames[index] orelse return error.InvalidChangeRecord;
            const payload = try log.readFrame(&self.file, self.recovery_payload, frame);
            switch (try Raw.PreparedWal.decode(payload)) {
                .prepare => |prepared| try store.restorePreparedEntry(index, prepared),
                .decision => return error.InvalidChangeRecord,
            }
        }

        fn finishRecovery(self: *Self) !void {
            const store = self.store orelse return error.PersistenceClosed;
            if (self.expected_identity != null and !self.seen_identity) return error.InvalidDatabaseIdentity;
            if (store.preparedCount() > store.prepared_limit) return error.TransactionCapacity;
            while (true) {
                var first: ?usize = null;
                for (self.pending_frames, 0..) |maybe_frame, index| {
                    const frame = maybe_frame orelse continue;
                    if (first == null or frame.sequence < self.pending_frames[first.?].?.sequence) first = index;
                }
                const index = first orelse break;
                try self.restorePending(store, index);
                self.pending_frames[index] = null;
            }
        }

        fn commit(context: ?*anyopaque, payload: []const u8) anyerror!void {
            const self: *Self = @ptrCast(@alignCast(context orelse return error.PersistenceClosed));
            _ = Raw.changeRecordOps(payload) catch return error.InvalidCommitBatch;
            _ = try self.appendRecord(change_kind, payload);
        }

        fn commitPrepared(context: ?*anyopaque, payload: []const u8) anyerror!u64 {
            const self: *Self = @ptrCast(@alignCast(context orelse return error.PersistenceClosed));
            _ = Raw.PreparedWal.decode(payload) catch return error.InvalidCommitBatch;
            return self.appendRecord(prepare_kind, payload);
        }

        fn appendRecord(self: *Self, kind: u16, payload: []const u8) !u64 {
            if (self.store == null) return error.PersistenceClosed;
            if (!self.active or self.file.poisoned) return error.StorageUnavailable;
            if (self.sequence == 0xffffffffffffffff) return error.GenerationExhausted;
            if (payload.len > self.max_batch_bytes) return error.TransactionLogCapacity;
            try log.appendKind(&self.file, self.sequence + 1, payload, kind);
            self.sequence += 1;
            return self.sequence;
        }

        /// Detaches the hook before releasing the writer lock. The store remains
        /// durability-required and refuses future writes until explicitly reopened.
        pub fn close(self: *Self) void {
            if (self.store) |store| {
                if (store.commit_ctx == @as(?*anyopaque, @ptrCast(self))) {
                    store.commit_fn = null;
                    store.prepare_fn = null;
                    store.commit_ctx = null;
                    store.encode_buffer = &.{};
                    store.storage_failed = true;
                }
            }
            self.file.close();
            self.writer_lock.close();
            self.checkpoint_layout = null;
            self.checkpoint_selection = null;
            self.store = null;
            self.active = false;
        }
    };
}
