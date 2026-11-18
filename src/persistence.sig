//! Product-owned durable SQL replay and store commit policy.
//! ZPM owns portable framing, change records and native I/O. The product keeps
//! a single locked WAL for the server lifetime and publishes commits only after
//! stable storage. WAL remains authoritative; no unsafe checkpoint/truncate
//! compaction is used.
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
pub const DatabaseIdentity = struct { cluster: [16]u8, oid: u32 };

pub const CheckpointPaths = struct {
    writer_lock: []const u8,
    selection: []const u8,
    image: []const u8,
    wal: []const u8,
};

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
        legacy_rows: [if (Raw.Result.external_rows) 1 else 0]Raw.Row = undefined,
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
                var payload: [20]u8 = undefined;
                payload[0..16].* = created.cluster;
                std.mem.writeInt(u32, payload[16..20], created.oid, .little);
                try log.appendKind(&self.file, 1, &payload, database_identity_kind);
            }
            const kinds = accepted_kinds | if (identity != null) @as(u32, 1) << database_identity_kind else @as(u32, 0);
            const recovery = try log.recoverFramesWithFinish(&self.file, recovery_buffer, self, Self.replay, Self.finishRecovery, kinds);
            self.attach(store, recovery_buffer, recovery, true);
            return recovery;
        }

        const CheckpointReplay = struct {
            persistence: *Self,
            /// The anchor contains the complete canonical selection record.
            anchor: [manifest.record_size]u8,

            fn verify(self: *const CheckpointReplay, _: log.Frame, payload: []const u8) !void {
                if (!std.mem.eql(u8, payload, &self.anchor)) return error.CheckpointAnchorMismatch;
            }

            fn apply(self: *CheckpointReplay, frame: log.Frame, payload: []const u8) !void {
                return self.persistence.replay(frame, payload);
            }
        };

        /// Internal startup composition. The expected identity comes from
        /// trusted database configuration. All paths belong to the same
        /// generation, and its WAL starts with this selection's anchor.
        /// The caller supplies a fresh unpublished store and discards it on
        /// any failure. The lock covers source verification through close.
        pub fn initCheckpoint(self: *Self, paths: CheckpointPaths, identity: manifest.DatabaseIdentity, store: *Store, recovery_buffer: []u8) !log.Recovery {
            try self.requireFresh(store, recovery_buffer);
            if (store.database_oid != identity.oid or store.prepared_limit != 0) return error.InvalidCheckpointStore;
            if (recovery_buffer.len < manifest.record_size) return error.InvalidWorkspace;
            store.durability_required = true;
            store.storage_failed = true;
            try self.writer_lock.acquire(paths.writer_lock);
            errdefer self.writer_lock.close();
            var selection_file = try disk.File.openExisting(paths.selection);
            defer selection_file.close();
            const selection = try manifest.Selection.read(&selection_file, identity);
            if (selection.anchor_offset != 0 or selection.anchor_kind != checkpoint_anchor_kind or
                selection.anchor_length != manifest.record_size) return error.InvalidCheckpointAnchor;
            var image = try disk.File.openExisting(paths.image);
            defer image.close();
            self.file = try disk.File.openExisting(paths.wal);
            errdefer self.file.close();
            const seal: snapshot.Seal = .{
                .identity = .{
                    .database_id = identity.database, .generation = selection.generation,
                    .wal_sequence = selection.base_sequence, .sql_csn = selection.sql_csn,
                },
                .payload_bytes = selection.image_bytes, .digest = selection.image_digest,
            };
            try snapshot.verify(&image, recovery_buffer, seal);
            const payload: ImagePayload = .{ .file = &image, .bytes = seal.payload_bytes };
            try Raw.BaseSnapshot.restore(&store.database, payload, seal.identity.sql_csn, recovery_buffer, &self.replay_scratch);
            self.store = store;
            errdefer self.store = null;
            self.max_batch_bytes = recovery_buffer.len;
            self.expected_identity = null;
            self.seen_identity = false;
            self.seen_change_record = true;
            self.sequence = selection.base_sequence;
            var state: CheckpointReplay = .{ .persistence = self, .anchor = try selection.encode() };
            const anchor: log.Frame = .{
                .offset = selection.anchor_offset, .sequence = try selection.anchorSequence(),
                .kind = selection.anchor_kind, .payload_len = selection.anchor_length,
            };
            const recovery = try log.recoverAfterFrame(&self.file, recovery_buffer, anchor, &state, CheckpointReplay.verify, CheckpointReplay.apply, null, @as(u32, 1) << change_kind);
            self.attach(store, recovery_buffer, recovery, false);
            return recovery;
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
            self.store = null;
            self.active = false;
        }
    };
}
