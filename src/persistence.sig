//! Product-owned durable SQL replay and store commit policy.
//! ZPM owns portable framing and native I/O. The product keeps a single locked
//! WAL for the server lifetime and publishes commits only after stable storage.
//! WAL remains authoritative; no unsafe checkpoint/truncate compaction is used.
const disk = @import("db_file");
const log = @import("durable_log");
const batches = @import("db_store");

pub fn Persistence(comptime Raw: type, comptime Store: type) type {
    return struct {
        const Self = @This();
        // Framing v1, payload kind 2 identifies PostgreSQL 32-bit INTEGER.
        // Refuse legacy development logs instead of silently reinterpreting SQL.
        const payload_kind: u16 = if (@hasDecl(Raw, "integer_type") and Raw.integer_type == .integer32) 2 else 1;
        file: disk.File = .{},
        store: ?*Store = null,
        workspace: ?*Raw = null,
        replay_result: Raw.Result = .{},
        sequence: u64 = 0,
        max_batch_bytes: usize = 0,
        active: bool = false,

        /// self and all caller storage must retain stable addresses until close.
        /// store must be newly initialized and workspace must be a distinct Raw.
        /// recovery_buffer must fit the largest permitted committed transaction.
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
            errdefer {
                self.file.close();
                self.store = null;
                self.workspace = null;
            }
            const recovery = try log.recoverKind(&self.file, recovery_buffer, self, Self.replay, payload_kind);
            self.sequence = recovery.sequence;
            store.generation = recovery.sequence;
            store.autocommit_workspace = workspace;
            store.commit_ctx = self;
            store.commit_fn = &Self.commit;
            store.storage_failed = false;
            self.active = true;
            return recovery;
        }

        fn replay(self: *Self, sequence: u64, payload: []const u8) !void {
            const store = self.store orelse return error.PersistenceClosed;
            const workspace = self.workspace orelse return error.InvalidWorkspace;
            workspace.copyFrom(&store.database);
            var iterator: batches.BatchIterator = .{ .bytes = payload };
            var count: usize = 0;
            while (try iterator.next()) |statement| {
                try workspace.execute(statement, &self.replay_result);
                count += 1;
            }
            if (count == 0) return error.InvalidCommitBatch;
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
            var iterator: batches.BatchIterator = .{ .bytes = payload };
            var count: usize = 0;
            while (try iterator.next()) |_| count += 1;
            if (count == 0) return error.InvalidCommitBatch;
            try log.appendKind(&self.file, self.sequence + 1, payload, payload_kind);
            self.sequence += 1;
        }

        /// Detaches the hook before releasing the writer lock. The store remains
        /// durability-required and refuses future writes until explicitly reopened.
        pub fn close(self: *Self) void {
            if (self.store) |store| {
                if (store.commit_ctx == @as(?*anyopaque, @ptrCast(self))) {
                    store.commit_fn = null;
                    store.commit_ctx = null;
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
