//! Product-owned durable SQL replay and store commit policy.
//! ZPM owns portable framing, change records and native I/O. The product keeps
//! a single locked WAL for the server lifetime and publishes commits only after
//! stable storage. WAL remains authoritative; no unsafe checkpoint/truncate
//! compaction is used.
//!
//! Payload kinds: 3 is the versioned change record (always written). Legacy
//! SQL-text kinds are replayed with the alpha grammar only: kind 2 for
//! PostgreSQL 32-bit INTEGER configurations, kind 1 for 64-bit ones. Exactly one
//! legacy kind is accepted per configuration, and a legacy record after any
//! kind-3 record fails closed.
const disk = @import("db_file");
const log = @import("durable_log");
const batches = @import("db_store");

pub const change_kind: u16 = 3;

pub fn Persistence(comptime Raw: type, comptime Store: type) type {
    return struct {
        const Self = @This();
        const legacy_kind: u16 = if (@hasDecl(Raw, "integer_type") and Raw.integer_type == .integer32) 2 else 1;
        pub const accepted_kinds: u32 = (@as(u32, 1) << legacy_kind) | (@as(u32, 1) << change_kind);
        file: disk.File = .{},
        store: ?*Store = null,
        workspace: ?*Raw = null,
        replay_result: Raw.Result = .{},
        /// Bounded overlay that validates each whole change record first.
        replay_scratch: Raw.ReplayScratch = .{},
        sequence: u64 = 0,
        max_batch_bytes: usize = 0,
        seen_change_record: bool = false,
        active: bool = false,

        /// self and all caller storage must retain stable addresses until close.
        /// store must be newly initialized and workspace must be a distinct Raw.
        /// recovery_buffer must fit the largest permitted committed transaction;
        /// after recovery it becomes the store's autocommit encode buffer, so
        /// every record the server writes fits on recovery.
        /// Call before binding listeners; any recovery error aborts startup.
        pub fn init(self: *Self, path: []const u8, store: *Store, workspace: *Raw, recovery_buffer: []u8) !log.Recovery {
            return self.initImpl(path, store, workspace, recovery_buffer, false);
        }

        /// Offline maintenance requires a real existing source; a misspelled
        /// database path must never silently become an empty valid backup.
        pub fn initExisting(self: *Self, path: []const u8, store: *Store, workspace: *Raw, recovery_buffer: []u8) !log.Recovery {
            return self.initImpl(path, store, workspace, recovery_buffer, true);
        }

        fn initImpl(self: *Self, path: []const u8, store: *Store, workspace: *Raw, recovery_buffer: []u8, existing: bool) !log.Recovery {
            if (self.active or self.store != null or store.commit_fn != null) return error.PersistenceAlreadyOpen;
            if (store.generation != 0) return error.PersistenceRequiresEmptyStore;
            if (workspace == &store.database or recovery_buffer.len == 0) return error.InvalidWorkspace;
            store.durability_required = true;
            store.storage_failed = true;
            self.file = if (existing) try disk.File.openExisting(path) else try disk.File.open(path);
            self.store = store;
            self.workspace = workspace;
            self.max_batch_bytes = recovery_buffer.len;
            self.seen_change_record = false;
            errdefer {
                self.file.close();
                self.store = null;
                self.workspace = null;
            }
            const recovery = try log.recoverKinds(&self.file, recovery_buffer, self, Self.replay, accepted_kinds);
            self.sequence = recovery.sequence;
            store.generation = recovery.sequence;
            store.autocommit_workspace = workspace;
            store.encode_buffer = recovery_buffer;
            store.commit_ctx = self;
            store.commit_fn = &Self.commit;
            store.storage_failed = false;
            self.active = true;
            return recovery;
        }

        fn replay(self: *Self, kind: u16, sequence: u64, payload: []const u8) !void {
            const store = self.store orelse return error.PersistenceClosed;
            if (kind == change_kind) {
                // Validated as a whole before anything applies; no copy needed.
                try store.database.applyChangeRecord(payload, &self.replay_scratch);
                self.seen_change_record = true;
                self.sequence = sequence;
                return;
            }
            if (kind != legacy_kind or self.seen_change_record) return error.UnsupportedLogVersion;
            const workspace = self.workspace orelse return error.InvalidWorkspace;
            var iterator: batches.BatchIterator = .{ .bytes = payload };
            const first = (try iterator.next()) orelse return error.InvalidCommitBatch;
            // A verified single-statement record is already fail-atomic in the
            // SQL engine. Check the complete length framing BEFORE applying it;
            // malformed tails must not publish even their first valid statement.
            if ((try iterator.next()) == null) {
                try store.database.executeLegacy(first, &self.replay_result);
                self.sequence = sequence;
                return;
            }
            // Multi-statement transactions still require a private candidate so
            // a rejected later statement cannot publish any earlier statement.
            workspace.copyFrom(&store.database);
            iterator = .{ .bytes = payload };
            while (try iterator.next()) |statement| {
                try workspace.executeLegacy(statement, &self.replay_result);
            }
            // Every statement and length frame passed before any batch publishes.
            store.database.copyFrom(workspace);
            self.sequence = sequence;
        }

        fn commit(context: ?*anyopaque, payload: []const u8) anyerror!void {
            const self: *Self = @ptrCast(@alignCast(context orelse return error.PersistenceClosed));
            const store = self.store orelse return error.PersistenceClosed;
            if (!self.active or self.file.poisoned) return error.StorageUnavailable;
            if (self.sequence != store.generation) return error.PersistenceSequenceMismatch;
            if (self.sequence == 0xffffffffffffffff) return error.GenerationExhausted;
            if (payload.len > self.max_batch_bytes) return error.TransactionLogCapacity;
            _ = Raw.changeRecordOps(payload) catch return error.InvalidCommitBatch;
            try log.appendKind(&self.file, self.sequence + 1, payload, change_kind);
            self.sequence += 1;
        }

        /// Detaches the hook before releasing the writer lock. The store remains
        /// durability-required and refuses future writes until explicitly reopened.
        pub fn close(self: *Self) void {
            if (self.store) |store| {
                if (store.commit_ctx == @as(?*anyopaque, @ptrCast(self))) {
                    store.commit_fn = null;
                    store.commit_ctx = null;
                    store.encode_buffer = &.{};
                    store.storage_failed = true;
                }
            }
            self.file.close();
            self.store = null;
            self.workspace = null;
            self.active = false;
        }
    };
}
