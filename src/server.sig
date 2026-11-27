//! Product policy, bounded reactors and shared-store read scheduling.
//!
//! One shared READ COMMITTED store serves every client; there are no
//! per-client database copies. A statement that waits for a lock parks its
//! client (Reply.blocked: nothing consumed): the client is not re-dispatched
//! until the store's wake epoch changes, one of its timers (deadlock check,
//! lock_timeout, statement_timeout) is due, or a cancel request arrives.
//! Woken clients run in wait-start order. The idle timeout is suspended
//! while a client is parked, but its socket is still read, so a disconnect
//! aborts its transaction and releases its locks. A COPY FROM row parked on
//! a key wait is already owned by the store: the reply consumes the input
//! through that row, the rest stays buffered (read gating is the
//! backpressure) and the woken client is dispatched even with no new input
//! so the parked row is re-checked first. PostgreSQL cancel keys:
//! pid = slot index + 1 with a 32-bit secret from the entropy source,
//! compared in constant time; unknown keys are ignored silently.
const sig = @import("sig");
const std = @import("std");
const profile = @import("profile");
const scram = @import("scram");
const entropy = @import("entropy");
const Persistence = @import("persistence.sig").Persistence(Raw, Store);
const sql = @import("sql");
const tcp = @import("tcp_server");
const pg = @import("postgres");
const mysql = @import("mysql");
const tds = @import("tds");
const stores = @import("db_store");
const wall_clock = @import("wall_clock");
const timezones = @import("timezones.sig");
const pages = @import("page_region");
const threads = @import("worker_thread");
const resource_budget = @import("resource_budget");
const owned_pages = @import("budgeted_region");
const machine_capacity = @import("capacity.sig");
const database_registry = @import("database_registry");
const database_catalog = @import("database_catalog.sig");
const disk = @import("db_file");
const postgres_tls = @import("postgres_tls.sig");

pub const Protocol = enum { postgres, mysql, mssql };
pub const table_capacity = profile.tables;
pub const Options = struct {
    protocol: Protocol = .postgres,
    port: ?u16 = null,
    mysql_port: ?u16 = null,
    mssql_port: ?u16 = null,
    max_clients: usize = profile.clients,
    /// Reactors and concurrent simple readers; mutations remain exclusive.
    /// Zero selects the native CPU and memory plan.
    workers: usize = 0,
    linux_direct_receive: bool = false,
    memory: bool = false,
    data: []const u8 = "ramsql.wal",
    checkpoint_stem: ?[]const u8 = null,
    checkpoint_id: ?[16]u8 = null,
    checkpoint_every: u64 = 0,
    /// Durable startup creates a missing `data` file only when set; otherwise
    /// a missing file is DataFileMissing before any listener opens.
    create_data: bool = false,
    database_cluster: bool = false,
    insecure_local: bool = false,
    auth_user: ?[]const u8 = null,
    auth_verifier_file: ?[]const u8 = null,
    auth_credentials_file: ?[]const u8 = null,
    auth_catalog_role: bool = false,
    tls_certificate_file: ?[]const u8 = null,
    tls_key_file: ?[]const u8 = null,
    idle_ms: u32 = 30000,
    /// Execution checkpoints per statement; zero disables the work limit.
    max_query_work: u64 = 0,
    /// Caller-selected budget for shared PostgreSQL read results.
    read_cache_bytes: usize = 0,
    /// Zero disables PostgreSQL prepared transactions. A positive limit
    /// reserves at least one ordinary transaction slot for other work.
    max_prepared_transactions: usize = 0,
    /// Runtime storage budget, independent of result/query workspace sizes.
    storage_rows: usize = profile.rows,
    storage_versions: ?usize = null,
    table_rows: ?[profile.tables]usize = null,
    table_versions: ?[profile.tables]usize = null,
    table_integral_words: ?[profile.tables]usize = null,
    typed_bank_memory_profile: bool = false,
    query_refs: ?usize = null,
    work_rows: usize = profile.work_rows,
    result_rows: usize = profile.result_rows,
    memory_budget: ?u64 = null,
    os_headroom: ?u64 = null,
    /// zoneinfo directory for named time zones (null: UTC, offsets and
    /// POSIX TZ strings only).
    timezone_dir: ?[]const u8 = default_timezone_dir,
};
pub const default_timezone_dir: ?[]const u8 = if (@import("builtin").os.tag == .linux or @import("builtin").os.tag == .macos) "/usr/share/zoneinfo" else null;
// Integer 0/1 BOOLEAN assignment is per session: the ZPM MySQL/TDS sessions
// set Store.Connection.dialect themselves; PostgreSQL keeps the default.
// Every client plus two spare slots can hold a transaction at once.
const config: sql.Config = storageConfig(.{
    .external_storage = true,
    .external_workspace = true,
    .external_results = true,
    .tables = profile.tables,
    .columns = profile.columns,
    .result_rows = profile.result_rows,
    .row_locks = profile.row_locks,
    .predicates = profile.predicates,
    .expr_nodes = profile.expr_nodes,
    .relations = profile.relations,
    .projection_columns = profile.projection_columns,
    .rows_per_table = profile.rows,
    .name_bytes = 63,
    .text_bytes = profile.text_bytes,
    .nulls_first_ascending = false,
    .integer_bits = 32,
    .versions_per_table = profile.versions,
    .tx_writes = profile.tx_writes,
    .transactions = profile.transactions,
    .work_rows = profile.work_rows,
    .summary_groups = profile.summary_groups,
    .compact_integral_scans = profile.compact_integral_scans,
    .factorized_aggregates = true,
    .block_presence_filters = true,
    .typed_grouped_aggregates = true,
    .dimension_group_ids = true,
    .dimension_distinct_groups = true,
    // Secondary indexes bind one slot run per index per table in the
    // external storage region (ZPM #118): 4 x pow2(2 x versions) x 4 bytes
    // per table. The storage plan already counts and binds them.
    .external_indexes = 4,
    // ZPM's database-wide session and catalog pools default to test sizes
    // (4 cursors, 8 listening sessions, 8 routines, 32 catalog objects for
    // the whole database). The server serves up to 60 clients at once.
    .cursors = 64,
    .cursor_rows = 1024,
    .prepared_statements = 32,
    .listeners = 64,
    .notify_queue = 1024,
    .notify_pending = 256,
    .advisory_locks = 256,
    .routines = 128,
    .triggers = 128,
    .rules = 32,
    .catalog_objects = 512,
});

fn storageConfig(defaults: sql.Config) sql.Config {
    if (comptime profile.integral_version_banks or profile.compact_version_stamps) {
        var experimental = defaults;
        experimental.split_external_versions = true;
        experimental.integral_external_versions = profile.integral_version_banks;
        experimental.compact_external_version_stamps = profile.compact_version_stamps;
        return experimental;
    }
    return defaults;
}

const Raw = sql.Database(config);
const Store = stores.Store(Raw);
const DB = Store.Connection;
const Zones = timezones.Timezones(Raw.Zone, profile.timezones);
// Caller-owned zone cache (profile.timezones x 16 KiB), filled on demand.
var zones: Zones = .{};

fn refersTo(settings: anytype, bytes: []const u8) bool {
    return settings.zone.kind == .tzif and settings.zone.bytes.ptr == bytes.ptr;
}

/// Zone-cache eviction guard: any open session's settings (current, session,
/// transaction-start, startup and savepoint copies) still referring to it.
fn zoneInUse(bytes: []const u8) bool {
    for (clients) |*client| {
        if (client.state.load(.acquire) != 2) continue;
        const c = &client.connection;
        if (refersTo(&c.settings, bytes) or refersTo(&c.base, bytes) or refersTo(&c.tx_start, bytes) or refersTo(&c.startup, bytes)) return true;
        for (c.saved[0..c.saved_len]) |*saved| if (refersTo(&saved.current, bytes) or refersTo(&saved.base, bytes)) return true;
    }
    return false;
}

/// Wall clock for now()/CURRENT_TIMESTAMP in PostgreSQL-epoch microseconds.
fn wallClock() ?i64 {
    const unix_us = wall_clock.nowUs() catch return null;
    return sql.temporal.fromUnixUs(unix_us);
}
const PG = pg.SessionWithAdmission(DB, true);
const MySQL = mysql.Session(DB);
const TDS = tds.Session(DB);
const standard_capacity = 60;
const capacity = profile.client_ceiling;
const input_capacity = 65536;
const output_capacity = @max(PG.responseCapacity(), @max(MySQL.responseCapacity(), TDS.responseCapacity())) + 32768;
const Session = union(Protocol) { postgres: PG, mysql: MySQL, mssql: TDS };
const Reply = struct { consumed: usize, written: usize, close: bool, blocked: bool, cancel: ?pg.Cancel = null };
// One execution context per reactor, reused only by its owning native thread.
// Results remain client-owned; the complete codec call ends before reuse.
const Reader=struct {
    metadata:owned_pages.Region=.{},
    scratch:owned_pages.Region=.{},
    context:?*Raw.ReadContext=null,
    retry_at:i64=0,
    fn provision(self:*Reader) !void {
        if(self.context!=null) return;
        lockMemory();defer unlockMemory();
        try refreshMemoryBudget();
        try self.metadata.reserve(&memory_ledger,@sizeOf(Raw.ReadContext));
        errdefer self.metadata.close() catch @panic("lost failed reader metadata");
        try self.scratch.reserve(&memory_ledger,try Raw.ReadContext.backingBytesFor(selected_work_rows,selected_read_refs));
        errdefer self.scratch.close() catch @panic("lost failed reader scratch");
        const contexts=try self.metadata.memory.view(Raw.ReadContext,0,1);
        contexts[0]=.{};
        try contexts[0].bindMemoryFor(self.scratch.memory.bytes,selected_work_rows,selected_read_refs);
        self.context=&contexts[0];
    }
    fn close(self:*Reader) void {
        lockMemory();defer unlockMemory();
        self.context=null;
        self.scratch.close() catch @panic("lost read workspace ownership");
        self.metadata.close() catch @panic("lost read context ownership");
        self.retry_at=0;
    }
};

const Client = struct {
    // Only the acceptor initializes state 1. Only the assigned reactor owns
    // state 2. Publish 0 only after disconnect and all page releases finish.
    state: std.atomic.Value(u32) = .init(0),
    cancel_key: std.atomic.Value(u64) = .init(0),
    cancel_request: std.atomic.Value(u64) = .init(0),
    socket: tcp.Socket = tcp.invalid_socket,
    timed_receive: enum { untested, unavailable, enabled } = .untested,
    connection: DB = .{ .store = &store, .id = 0 },
    /// The active member of `session`. Tag tests read this field: comparing
    /// the union by value (`session == .postgres`) copies the whole
    /// ~1.1 MiB session into the frame in Debug builds, which overflowed
    /// the 4 MiB reactor thread stack.
    protocol: Protocol = .postgres,
    session: Session = .{ .postgres = .{} },
    input: [input_capacity]u8 = undefined,
    input_len: usize = 0,
    input_start: usize = 0,
    output: []u8 = &.{},
    result_region: owned_pages.Region = .{},
    output_region: owned_pages.Region = .{},
    startup_region: owned_pages.Region = .{},
    transport: ?*postgres_tls.Transport = null,
    database_binding: ?ClusterRegistry.Binding = null,
    reader:?*Reader=null,
    read_deadline:?i64=null,
    idle_transaction_deadline: ?i64 = null,
    idle_transaction_after_flush_ms: u32 = 0,
    output_len: usize = 0,
    output_sent: usize = 0,
    closing: bool = false,
    touched: i64 = 0,
    /// A statement of this client is parked on a lock.
    blocked: bool = false,
    cancel_secret: u32 = 0,
    /// notification_sequence when this idle client last drained NOTIFY.
    notify_seen: u64 = 0,

    /// PostgreSQL sends NotificationResponse to an idle LISTEN session
    /// without waiting for its next query. A statement that commits NOTIFY
    /// moves notification_sequence; only then does an idle client take the
    /// store lock and drain its own queue (the codec writes nothing inside a
    /// transaction block, where delivery waits for ReadyForQuery).
    fn deliverNotifications(self: *Client) void {
        if (self.protocol != .postgres or self.blocked or self.closing or self.output_len != 0 or self.input_len != 0) return;
        if (cluster_options.database_cluster and self.database_binding == null) return;
        if (!cluster_options.database_cluster and notification_sequence.load(.acquire) == self.notify_seen) return;
        lockStore();
        defer unlockStore();
        const sequence = self.connection.store.database.notificationSequence();
        if (sequence == self.notify_seen) return;
        self.notify_seen = sequence;
        const written = self.session.postgres.notifications(&self.connection, self.output[self.output_len..]) catch return;
        self.output_len += written;
    }

    fn close(self: *Client) void {
        const active = self.state.load(.acquire) == 2;
        if (active) lockStore();
        self.cancel_key.store(0, .release);
        if (active) self.connection.disconnect();
        if (self.database_binding) |binding| {
            cluster_registry.release(binding.lease) catch @panic("lost database lease");
            self.database_binding = null;
        }
        self.state.store(1, .release);
        if (active) unlockStore();
        if (self.transport) |transport| {
            transport.close();
            std.crypto.secureZero(u8, &self.input);
        }
        self.transport = null;
        if(self.socket!=tcp.invalid_socket) tcp.close(self.socket);
        lockMemory();
        self.result_region.close() catch @panic("lost client result ownership");
        self.output_region.close() catch @panic("lost client response ownership");
        self.startup_region.close() catch @panic("lost client startup ownership");
        self.reader=null;
        unlockMemory();
        self.output = &.{};
        self.socket = tcp.invalid_socket;
        self.blocked = false;
        self.idle_transaction_deadline = null;
        self.idle_transaction_after_flush_ms = 0;
        // The owner publishes a closed slot as free on its next iteration,
        // after callers of close have stopped inspecting the old connection.
        self.state.store(if (parallel and active) 3 else 0, .release);
    }

    fn sessionDeadline(self: *const Client) ?i64 {
        if (self.protocol != .postgres or self.connection.blocked() or self.closing) return null;
        // Like PostgreSQL, a COPY socket read defers ordinary transaction
        // timeout delivery until input or a parked COPY row resumes.
        const transaction = if (self.session.postgres.waitingForCopyInput()) null else self.connection.transactionDeadline();
        // A simple Query still running against socket backpressure is active.
        if (self.session.postgres.continuingSimpleQuery()) return transaction;
        const idle = self.idle_transaction_deadline orelse return transaction;
        return if (transaction) |at| @min(at, idle) else idle;
    }

    fn expireSessionTimeout(self: *Client, now: i64) void {
        const deadline = self.sessionDeadline() orelse return;
        if (now < deadline) return;
        const transaction_expired = self.connection.transactionDeadline() == deadline;
        self.idle_transaction_deadline = null;
        self.idle_transaction_after_flush_ms = 0;
        self.closing = true;
        self.touched = now;
        self.cancel_key.store(0, .release);
        lockStore();
        // Release the transaction immediately, even if the peer has not yet
        // drained its earlier response or stops reading after the timeout.
        self.connection.terminateTimedOut();
        unlockStore();
        if (self.output_sent != 0) {
            const unsent = self.output[self.output_sent..self.output_len];
            std.mem.copyForwards(u8, self.output, unsent);
            self.output_len = unsent.len;
            self.output_sent = 0;
        }
        const response = self.output[self.output_len..];
        const written = (if (transaction_expired) pg.transactionTimeoutError(response) else pg.idleTransactionTimeoutError(response)) catch {
            self.close();
            return;
        };
        self.output_len += written;
    }

    fn queuedSimpleQuery(self: *const Client) bool {
        return self.protocol == .postgres and self.session.postgres.queuedSimpleQuery();
    }

    fn dispatch(self: *Client) !void {
        if (self.blocked or self.output_sent != 0 or self.closing or (self.input_len == 0 and !self.connection.copyBlocked() and !self.queuedSimpleQuery())) return;
        // Cancellation is control traffic and must not queue behind SQL held
        // by another reactor. An established session cannot send startup frames.
        if (parallel and self.transport == null and self.protocol == .postgres and !self.session.postgres.startup) {
            if (pg.parseCancel(self.input[self.input_start..][0..self.input_len])) |key| {
                cancelBackend(key); self.consume(16); self.closing = true; return;
            }
        }
        if (!self.queuedSimpleQuery() and try self.dispatchRead(false)) return;
        lockStore();
        defer {
            maybeCheckpoint();
            unlockStore();
        }
        if (parallel) {
            store.interrupt_context = self;
            pollWorkerInterrupts(self, self.connection.id);
        }
        const required = switch (self.session) {
            .postgres => |*session| try session.requiredCapacity(),
            .mysql => |*session| try session.requiredCapacity(),
            .mssql => |*session| try session.requiredCapacity(),
        };
        const parked_copy = self.connection.copyBlocked();
        if (self.blocked or self.output_sent != 0 or self.closing or (self.input_len == 0 and !parked_copy and !self.queuedSimpleQuery()) or self.output.len - self.output_len < required) return;
        const input = self.input[self.input_start..][0..self.input_len];
        const output = self.output[self.output_len..];
        self.connection.store.now_ns = sig.os.clockMonotonicNs();
        const reply: Reply = switch (self.session) {
            .postgres => |*session| blk: {
                const r = try session.handle(&self.connection, input, output);
                break :blk .{ .consumed = r.consumed, .written = r.written, .close = r.close, .blocked = r.blocked, .cancel = r.cancel };
            },
            .mysql => |*session| blk: {
                const r = try session.handle(&self.connection, input, output);
                break :blk .{ .consumed = r.consumed, .written = r.written, .close = r.close, .blocked = r.blocked };
            },
            .mssql => |*session| blk: {
                const r = try session.handle(&self.connection, input, output);
                break :blk .{ .consumed = r.consumed, .written = r.written, .close = r.close, .blocked = r.blocked };
            },
        };
        notification_sequence.store(store.database.notificationSequence(), .release);
        if (reply.consumed > self.input_len or reply.written > output.len) return error.InvalidReply;
        if (self.protocol == .postgres and (reply.consumed != 0 or reply.written != 0 or reply.blocked)) {
            self.idle_transaction_deadline = null;
            self.idle_transaction_after_flush_ms = if (reply.close) 0 else
                self.session.postgres.idleTransactionTimeout(&self.connection);
        }
        if (reply.blocked) {
            // Only a parked COPY row consumes input (through that row).
            if (reply.written != 0 or !self.connection.blocked() or (reply.consumed != 0 and !self.connection.copyBlocked())) return error.InvalidReply;
            self.consume(reply.consumed);
            self.blocked = true;
            return;
        }
        if (reply.cancel) |key| cancelBackend(key);
        if (reply.consumed != 0) {
            self.consume(reply.consumed);
        } else if (reply.written == 0 and self.input_len == self.input.len) return error.RequestTooLarge;
        self.output_len += reply.written;
        self.closing = reply.close;
    }

    fn pollRead(raw:?*anyopaque) Raw.SqlError!void {
        const self:*Client=@ptrCast(@alignCast(raw.?));
        const requested=self.cancel_request.swap(0,.acq_rel);
        if(requested!=0 and requested==self.cancel_key.load(.acquire)) return error.QueryCanceled;
        if(self.read_deadline) |deadline| if(monotonicClock()>=deadline) return error.QueryCanceled;
    }
    fn dispatchRead(self:*Client, prepared_only: bool) !bool {
        if(!parallel or self.protocol!=.postgres or !self.connection.detachedReadEligible()) return false;
        const session=&self.session.postgres;
        const input=self.input[self.input_start..][0..self.input_len];
        const key_exchange = if (store.max_query_work == 0 and self.connection.settings.statement_timeout_ms == 0)
            session.detachedKeyExchange(input)
        else
            null;
        if (prepared_only) {
            const exchange = key_exchange orelse return false;
            // A single complete exchange can reply immediately. Leave batches
            // to serve(), which can combine their replies in one socket write.
            if (exchange.consumed != input.len) return false;
        }
        const candidate = if (key_exchange != null) PG.DetachedCandidate.metadata else
            session.detachedCandidate(input) orelse return false;
        const source:?[]const u8=switch(candidate){.metadata=>null,.describe,.execute=>|text|text};
        if(source) |text| {
            if(!Raw.queryCandidate(text)) return false;
        }
        const required=try session.requiredCapacity();
        if(self.output.len-self.output_len<required) return false;
        const reader=self.reader orelse return false;
        if (prepared_only) {
            // Readiness dispatch never provisions scratch or parks behind a
            // writer. The owning reactor will retry during normal service.
            if (reader.context == null or !data_gate.tryLockRead()) return false;
        } else {
            if(reader.context==null and monotonicClock()<reader.retry_at) return false;
            reader.provision() catch {reader.retry_at=monotonicClock()+1000000000;return false;};
            data_gate.lockRead();
        }
        defer data_gate.unlockRead();
        if (store.database.detachedReadsBlocked()) return false;
        const context=reader.context.?;
        // Protocol Describe may lend stack-local interpolation text. No plan
        // or source reference survives the complete codec call or worker reuse.
        defer {context.prepared=null;context.preparation_error=null;context.database=null;context.source=&.{};}
        self.cancel_request.store(0,.release);
        self.connection.execution.reset(store.max_query_work);
        self.connection.execution.context=self;self.connection.execution.poll=pollRead;
        self.read_deadline=if(self.connection.settings.statement_timeout_ms==0) null else monotonicClock()+@as(i64,self.connection.settings.statement_timeout_ms)*1000000;
        // The retained key projection contains only direct columns. Even a
        // cache miss here hands off an ordinary Bind, which evaluates no SQL.
        const statement_us = if (key_exchange == null) wallClock() else null;
        context.session=self.connection.detachedReadSession(&self.connection.execution,statement_us);
        context.dialect=.postgres;
        const snapshot: Raw.Snapshot = .{.csn=store.database.csn};
        var prepared_primary_key = false;
        if (comptime @hasDecl(PG, "detachedPrimaryKey") and @hasDecl(Raw.ReadContext, "preparePrimaryKey")) {
            // A failed hint must not spend a second parse budget on queries
            // running under an explicit work limit. The normal lane owns them.
            if (store.max_query_work == 0) if (session.detachedPrimaryKey(input)) |key| {
                const data_type: ?sql.DataType = switch (key.oid) {
                    20 => .integer,
                    21 => .smallint,
                    23 => .integer32,
                    else => null,
                };
                if (data_type) |ty| prepared_primary_key = context.preparePrimaryKey(&store.database,key.source,snapshot,ty) catch false;
            };
        }
        var prepared=true;
        if (!prepared_primary_key) if(source) |text| context.prepare(&store.database,text,snapshot) catch |err| switch(err) {
            error.QueryCanceled,error.QueryWorkLimit=>{prepared=false;},
            else=>return false,
        };
        // Dynamic named-zone resolution retains the existing serialized cache
        // ownership. Typed temporal values use the immutable session zone.
        if(source!=null and prepared and !prepared_primary_key and context.needsZoneResolver()) return false;
        self.connection.read_context=context;self.connection.running_statement=true;
        defer {self.connection.read_context=null;self.connection.running_statement=false;self.read_deadline=null;}
        const output=self.output[self.output_len..];
        const fused_reply = if (key_exchange) |exchange|
            try session.handleDetachedKeyExchange(&self.connection, exchange, output)
        else
            null;
        if (prepared_only and fused_reply == null) return false;
        const reply = if (fused_reply) |completed| completed else if (prepared_primary_key)
            try session.handleDetachedExecuteSync(&self.connection, input, output)
        else
            try session.handle(&self.connection, input, output);
        if(reply.consumed>self.input_len or reply.written>output.len or reply.blocked or reply.cancel!=null) return error.InvalidReply;
        if(reply.consumed!=0) self.consume(reply.consumed) else if(self.input_len==self.input.len) return error.RequestTooLarge;
        self.output_len+=reply.written;self.closing=reply.close;
        return true;
    }

    fn consume(self: *Client, count: usize) void {
        self.input_len -= count;
        self.input_start += count;
        if (self.input_len == 0) self.input_start = 0;
    }

    fn armIdleTimeoutAfterFlush(self: *Client) void {
        if (self.closing or self.idle_transaction_after_flush_ms == 0 or
            (self.protocol == .postgres and self.session.postgres.continuingSimpleQuery())) return;
        self.idle_transaction_deadline = monotonicClock() +|
            (@as(i64, self.idle_transaction_after_flush_ms) * 1000000);
        self.idle_transaction_after_flush_ms = 0;
    }

    fn flush(self: *Client) !void {
        if (self.transport) |transport| {
            const result = try transport.flush(self.socket, self.output[self.output_sent..self.output_len], self.closing);
            if (result.sent) self.touched = monotonicClock();
            self.output_sent += result.accepted;
            if (self.output_sent == self.output_len and !transport.needsWrite()) {
                self.output_len = 0;
                self.output_sent = 0;
                if (self.closing and transport.close_queued) {
                    self.close();
                } else self.armIdleTimeoutAfterFlush();
            }
            return;
        }
        if (self.output_len != 0) {
            const sent = try tcp.send(self.socket, self.output[self.output_sent..self.output_len]) orelse return;
            if (sent == 0) return error.SendFailed;
            self.output_sent += sent;
            self.touched = sig.os.clockMonotonicNs();
            if (self.output_sent == self.output_len) {
                self.output_len = 0;
                self.output_sent = 0;
            }
        }
        if (self.output_len != 0) return;
        if (self.closing) {
            self.close();
            return;
        }
        // ReadyForQuery enters the idle read phase only once output drains.
        self.armIdleTimeoutAfterFlush();
    }

    fn pullTls(self: *Client, transport: *postgres_tls.Transport) !void {
        if (self.closing) return;
        if (self.input_start != 0) {
            std.mem.copyForwards(u8, self.input[0..self.input_len], self.input[self.input_start..][0..self.input_len]);
            self.input_start = 0;
        }
        const established = transport.established();
        self.input_len += try transport.pull(self.input[self.input_len..]);
        if (!established and transport.established()) {
            // The same leaf was sent by this completed TLS exchange. The
            // PostgreSQL codec must see the binding before any startup bytes.
            try self.session.postgres.configureChannelBinding(tls_credentials.endpoint(), false);
        }
    }

    fn wantsRead(self: *const Client) bool {
        if (self.output_len != 0 or self.input_len == self.input.len) return false;
        if (self.transport) |transport| return !self.closing and transport.canRead();
        return true;
    }

    fn wantsWrite(self: *const Client) bool {
        if (self.output_len != 0) return true;
        if (self.transport) |transport| return self.closing or transport.needsWrite();
        return false;
    }

    fn receive(self: *Client, comptime wait_for_data: bool) !void {
        if (!self.wantsRead()) return;
        if (self.transport) |transport| {
            if (try transport.receive(self.socket)) self.touched = monotonicClock();
            return;
        }
        // Compact once per socket read, not once per pipelined packet.
        if (self.input_start != 0) {
            std.mem.copyForwards(u8, self.input[0..self.input_len], self.input[self.input_start..][0..self.input_len]);
            self.input_start = 0;
        }
        const bytes = self.input[self.input_len..];
        const incoming = if (wait_for_data) try tcp.recvTimed(self.socket, bytes) else try tcp.recv(self.socket, bytes);
        if (incoming) |received| {
            if (received == 0) return error.EndOfStream;
            self.input_len += received;
            self.touched = monotonicClock();
        }
    }

    /// Readability starts a bounded protocol turn for a single, already
    /// recognized read. The regular reactor retains fragmented and pipelined
    /// exchanges, TLS, and connections with pending session control work.
    fn receiveReady(self: *Client, comptime wait_for_data: bool) !void {
        const empty = self.input_len == 0;
        try self.receive(wait_for_data);
        if (!empty or self.input_len == 0 or self.transport != null or self.output_len != 0 or
            self.closing or self.blocked or self.cancel_request.load(.acquire) != 0 or self.sessionDeadline() != null)
            return;
        if (try self.dispatchRead(true)) try self.flush();
    }

    /// Only wait inside recv at an idle PostgreSQL command boundary. Ordinary
    /// polling, partial reads, and all writes keep their nonblocking behavior.
    fn directReceive(self: *Client) bool {
        if (self.transport != null or self.protocol != .postgres or self.blocked or self.closing or
            self.input_len != 0 or self.output_len != 0 or self.cancel_request.load(.acquire) != 0 or
            self.sessionDeadline() != null or !self.connection.detachedReadEligible() or
            !self.session.postgres.idleCommandBoundary()) return false;
        if (self.timed_receive == .untested) {
            self.timed_receive = .unavailable;
            tcp.enableTimedReceive(self.socket, 5) catch return false;
            self.timed_receive = .enabled;
        }
        if (self.timed_receive != .enabled) return false;
        self.receiveReady(true) catch self.close();
        return true;
    }

    /// Dispatches at most 32 buffered packets, then flushes. Returns true
    /// when packets remain buffered (the reactor should poll without waiting).
    fn serve(self: *Client) bool {
        if (self.transport) |transport| {
            self.pullTls(transport) catch { self.close(); return false; };
            if (!transport.established()) {
                self.flush() catch self.close();
                return self.socket != tcp.invalid_socket and transport.more and !transport.needsWrite();
            }
        }
        var frames: usize = 0;
        while (frames < 32) : (frames += 1) {
            if (self.sessionDeadline() != null) self.expireSessionTimeout(monotonicClock());
            if (self.socket == tcp.invalid_socket) return false;
            const before = self.input_len;
            const previous_output = self.output_len;
            self.dispatch() catch {
                self.close();
                return false;
            };
            if (self.blocked or (before == self.input_len and previous_output == self.output_len) or self.closing) break;
        }
        if (self.transport) |transport| {
            if (transport.peer_closed) {
                if (self.blocked) { self.close(); return false; }
                if (self.input_len == 0 or
                    (frames < 32 and self.output_len == 0 and !transport.needsWrite()))
                    self.closing = true;
            }
        }
        self.deliverNotifications();
        self.flush() catch {
            self.close();
            return false;
        };
        if (self.socket == tcp.invalid_socket) return false;
        if (self.transport) |transport| {
            if (!self.closing and !transport.needsWrite()) {
                if (transport.peer_closed and self.input_len != 0 and self.output_len == 0) return true;
                if (transport.more and self.input_len < self.input.len) return true;
            }
        }
        const continuing_query = self.protocol == .postgres and self.session.postgres.continuingSimpleQuery();
        const more_input = (frames == 32 or continuing_query) and self.input_len != 0;
        return !self.blocked and self.output_len == 0 and (more_input or self.queuedSimpleQuery());
    }
};
var store: Store = .{};
var persistence: Persistence = .{};
var checkpoint_every: u64 = 0;
var last_checkpoint_attempt: u64 = 0;

/// Only called with the writer gate held. A failed staging attempt leaves
/// the old WAL authoritative. An uncertain manifest switch closes writes.
fn maybeCheckpoint() void {
    if (checkpoint_every == 0 or store.storage_failed) return;
    const base = persistence.checkpointBaseSequence() orelse return;
    const sequence = persistence.sequence;
    if (sequence -| @max(base, last_checkpoint_attempt) < checkpoint_every) return;
    last_checkpoint_attempt = sequence;
    persistence.rotateCheckpoint(&recovery_buffer, null) catch |err| {
        const line = if (store.storage_failed) " (checkpoint selector uncertain; writes disabled)\n" else " (checkpoint deferred; prior WAL remains active)\n";
        dataNotice(@errorName(err), line);
    };
}
const cluster_capacity = 4;
const primary_database = "postgres";
const cluster_owner: u32 = 10;
const ClusterRegistry = database_registry.Registry(Store, config.name_bytes);
const ClusterCatalog = database_catalog.Catalog(cluster_capacity, config.name_bytes);
const ClusterStore = struct { store: Store = .{}, persistence: Persistence = .{} };
const ClusterSlot = struct {
    metadata: owned_pages.Region = .{},
    backing: owned_pages.Region = .{},
    workspace: owned_pages.Region = .{},
    log_buffer: owned_pages.Region = .{},
    database: ?*ClusterStore = null,

    fn close(self: *ClusterSlot) void {
        if (self.database) |database| database.persistence.close();
        self.database = null;
        self.log_buffer.close() catch @panic("lost cluster log memory");
        self.workspace.close() catch @panic("lost cluster workspace");
        self.backing.close() catch @panic("lost cluster storage");
        self.metadata.close() catch @panic("lost cluster metadata");
    }
};
var cluster_options: Options = .{};
var cluster_registry: ClusterRegistry = undefined;
var cluster_entries: [cluster_capacity]ClusterRegistry.Entry = undefined;
var cluster_leases: [capacity]ClusterRegistry.LeaseEntry = undefined;
var cluster_catalog: ClusterCatalog = .{};
var cluster_slots: [cluster_capacity]ClusterSlot = @splat(.{});
// profile.transaction_bytes sizes this recovery buffer, which Persistence also
// hands to the store as the change-record encode buffer: any record written is
// recoverable.
var recovery_buffer: [profile.transaction_bytes]u8 = undefined;
var verifier: ?scram.Verifier = null;
var catalog_verifiers: [config.catalog_objects]scram.Verifier = undefined;
var catalog_credentials: ?scram.Credentials = null;
var bind_catalog_role = false;
// Keep large caller-owned buffers out of the executable data image.
// run initializes free-slot metadata; accept initializes every live field.
var clients: []Client = &.{};
var selected_result_rows: usize = profile.result_rows;
var selected_work_rows:usize=profile.work_rows;
var selected_read_refs:usize=0;
var parallel = false;
var store_mutex: threads.Mutex = .{};
var data_gate:threads.RwLock=.{};
fn lockStore() void { if (parallel) {data_gate.lockWrite();store_mutex.lock();} }
fn unlockStore() void { if (parallel) {store_mutex.unlock();data_gate.unlockWrite();} }
var memory_mutex:threads.Mutex=.{};
var memory_ledger:resource_budget.Ledger=.{};
var memory_slots:[capacity*2+max_reactors*2+4]resource_budget.Slot=undefined;
var memory_hard:?u64=null;
var memory_headroom:?u64=null;
var memory_refresh_at:i64=0;
// Each reactor: its native stack (ZPM worker_thread.stack_bytes, 16 MiB in
// Debug and 4 MiB optimized) plus the 1 MiB margin the former fixed 5 MiB
// grant carried above the 4 MiB stack.
const worker_budget_each:u64=threads.stack_bytes+1024*1024;
fn lockMemory() void {if(parallel) memory_mutex.lock();}
fn unlockMemory() void {if(parallel) memory_mutex.unlock();}
fn initializeMemory(options:Options) !machine_capacity.Sample {
    memory_hard=options.memory_budget;memory_headroom=options.os_headroom;
    const observed=try machine_capacity.sample(memory_hard,memory_headroom,0);
    try memory_ledger.bind(&memory_slots,observed.new_region_bytes);
    memory_refresh_at=monotonicClock()+1000000000;
    return observed;
}
// The memory mutex keeps our region ownership stable while sampling. Pressure
// reduces only future grants; existing mappings are never discarded/revoked.
fn refreshMemoryBudget() !void {
    const now=monotonicClock();if(now<memory_refresh_at) return;
    const used=memory_ledger.stats().used;
    const observed=machine_capacity.sample(memory_hard,memory_headroom,used) catch |err| {
        try memory_ledger.resize(used);return err;
    };
    const limit=@addWithOverflow(used,observed.new_region_bytes);
    if(limit[1]!=0) return error.ArithmeticOverflow;
    try memory_ledger.resize(limit[0]);memory_refresh_at=now+1000000000;
}
fn clientMemoryAvailable() bool {
    lockMemory();defer unlockMemory();
    refreshMemoryBudget() catch return false;
    const page=pages.pageSize();
    const result=Raw.Result.rowBytes(selected_result_rows) catch return false;
    const output=responseBytes(selected_result_rows) catch return false;
    const result_charge=machine_capacity.pageCharge(result,page) catch return false;
    const output_charge=machine_capacity.pageCharge(output,page) catch return false;
    const required=@addWithOverflow(result_charge,output_charge);
    return required[1]==0 and memory_ledger.stats().free>=required[0];
}

// Called only by the executing connection while it owns store_mutex. A cancel
// generation cannot spill into a newly accepted connection reusing this slot.
fn pollWorkerInterrupts(context: ?*anyopaque, _: u64) void {
    const client: *Client = @ptrCast(@alignCast(context.?));
    const requested = client.cancel_request.swap(0, .acq_rel);
    if (requested != 0 and requested == client.cancel_key.load(.acquire)) client.connection.requestCancel();
}

fn responseBytes(rows: usize) !usize {
    const required = @max(try PG.responseCapacityFor(rows), @max(try MySQL.responseCapacityFor(rows), try TDS.responseCapacityFor(rows)));
    const amount = @addWithOverflow(required, 32768);
    if (amount[1] != 0) return error.InvalidServerOptions;
    return amount[0];
}

fn provisionClient(client: *Client) !void {
    lockMemory();defer unlockMemory();
    try refreshMemoryBudget();
    try client.result_region.reserve(&memory_ledger,try Raw.Result.rowBytes(selected_result_rows));
    errdefer client.result_region.close() catch @panic("lost failed client result ownership");
    try client.output_region.reserve(&memory_ledger,try responseBytes(selected_result_rows));
    errdefer client.output_region.close() catch @panic("lost failed client response ownership");
    switch (client.session) {
        .postgres => |*session| try session.result.bindMemory(client.result_region.memory.bytes, selected_result_rows),
        .mysql => |*session| try session.result.bindMemory(client.result_region.memory.bytes, selected_result_rows),
        .mssql => |*session| try session.result.bindMemory(client.result_region.memory.bytes, selected_result_rows),
    }
    client.output = client.output_region.memory.bytes;
}
var next_id: u64 = 1;
var tls_credentials: postgres_tls.Credentials = .{};
var tls_slots: []postgres_tls.Slot = &.{};

fn sameSecret(a: u32, b: u32) bool {
    // Constant time in the secret's bits.
    return (a ^ b) == 0;
}

/// CancelRequest from another socket: pid = slot + 1. Unknown keys are ignored.
fn cancelBackend(key: pg.Cancel) void {
    if (key.pid == 0 or key.pid > clients.len) return;
    const target = &clients[key.pid - 1];
    if (parallel) {
        const wanted = (@as(u64, key.secret) << 1) | 1;
        if (target.cancel_key.load(.acquire) != wanted) return;
        target.cancel_request.store(wanted, .release);
        for (&reactors) |*reactor| reactor.signal.notify();
        return;
    }
    if (target.socket == tcp.invalid_socket or target.protocol != .postgres) return;
    if (!sameSecret(target.cancel_secret, key.secret)) return;
    target.connection.requestCancel();
}

fn monotonicClock() i64 {
    return sig.os.clockMonotonicNs();
}

fn deadlinePollMs(now: i64, due: i64, ceiling: u32) u32 {
    if (due <= now) return 0;
    return @intCast(@min(@divFloor((due -| now) +| 999999, 1000000), ceiling));
}

var interrupt_listener: tcp.Socket = tcp.invalid_socket;
var interrupt_max_clients: usize = 0;
var next_interrupt_poll: i64 = 0;
var admission_timeout_ns: i64 = 1000000000;

// A bounded control/admission queue preserves cancellation when the SQL pool is
// full, negotiates SSL/GSS refusal, and returns a real 53300 startup error.
const OverflowCancel = struct {
    const Admission = enum { handled, connection_limit, memory_limit };

    socket: tcp.Socket = tcp.invalid_socket,
    bytes: [pg.max_packet]u8 = undefined,
    used: usize = 0,
    output: [256]u8 = undefined,
    output_len: usize = 0,
    output_sent: usize = 0,
    closing: bool = false,
    deadline: i64 = 0,

    fn close(self: *OverflowCancel) void {
        if (self.socket != tcp.invalid_socket) tcp.close(self.socket);
        self.socket = tcp.invalid_socket;
    }

    fn handOff(self: *OverflowCancel) Admission {
        for (clients,0..) |*client,slot| if (client.state.load(.acquire) == 0) {
            if (!clientMemoryAvailable()) return .memory_limit;
            const socket = self.socket;
            configureClient(socket,.postgres,slot,self.bytes[0..self.used],false) catch |err| {
                switch(err) {
                    error.BudgetExceeded,error.TokenCapacity,error.MemoryReservationFailed=>return .memory_limit,
                    else=>self.close(),
                }
                return .handled;
            };
            self.socket = tcp.invalid_socket;
            return .handled;
        };
        return .connection_limit;
    }

    fn poll(self: *OverflowCancel, now: i64) void {
        if (self.socket == tcp.invalid_socket) return;
        if (now >= self.deadline) { self.close(); return; }
        if (self.output_sent < self.output_len) {
            const sent = tcp.send(self.socket, self.output[self.output_sent..self.output_len]) catch { self.close(); return; } orelse return;
            if (sent == 0) { self.close(); return; }
            self.output_sent += sent;
            if (self.output_sent != self.output_len) return;
            self.output_len = 0; self.output_sent = 0;
            if (self.closing) { self.close(); return; }
        }
        var reads: usize = 0;
        while (true) {
            const frame = pg.classifyStartup(self.bytes[0..self.used]);
            switch (frame) {
                .incomplete => |target| {
                    // Transfer a short declared frame after its length, as before.
                    if (self.used >= 4 and target < 8) {
                        if (self.handOff() != .handled) self.close();
                        return;
                    }
                    if (reads == 4) return;
                    reads += 1;
                    const got = tcp.recv(self.socket, self.bytes[self.used..target]) catch { self.close(); return; } orelse return;
                    if (got == 0) { self.close(); return; }
                    self.used += got;
                },
                .ssl_request, .gss_request => {
                    self.output[0] = 'N';
                    self.output_len = 1;
                    self.used = 0;
                    return;
                },
                .cancel => |key| {
                    cancelBackend(key);
                    self.close();
                    return;
                },
                .startup => {
                    // A free SQL slot validates the StartupMessage parameters.
                    const response = switch (self.handOff()) {
                        .handled => return,
                        .connection_limit => pg.connectionLimitError(&self.output),
                        .memory_limit => pg.memoryLimitError(&self.output),
                    };
                    self.output_len = response catch {
                        self.close();
                        return;
                    };
                    self.output_sent = 0;
                    self.closing = true;
                    return;
                },
                .invalid_length, .protocol_violation, .unsupported_protocol => {
                    if (self.handOff() != .handled) self.close();
                    return;
                },
            }
        }
    }
};
var overflow_cancels: [capacity + 4]OverflowCancel = @splat(.{});

fn queueOverflowCancel(socket: tcp.Socket) bool {
    for (&overflow_cancels) |*pending| if (pending.socket == tcp.invalid_socket) {
        pending.* = .{ .socket = socket, .deadline = monotonicClock() + admission_timeout_ns };
        return true;
    };
    return false;
}

fn pollOverflowCancels(now: i64) void {
    for (&overflow_cancels) |*pending| pending.poll(now);
}

/// Execution hook: bounded nonblocking control I/O, at most once per millisecond.
/// Preserve ordinary startup bytes for the reactor; never dispatch another SQL
/// statement or touch the executing client's shared database/session here.
fn pollInterrupts(_: ?*anyopaque, _: u64) void {
    const now = monotonicClock();
    if (now < next_interrupt_poll) return;
    next_interrupt_poll = now + 1000000;
    if (interrupt_listener != tcp.invalid_socket)
        acceptConnections(interrupt_listener, .postgres, interrupt_max_clients, 4, null) catch {};
    pollOverflowCancels(now);
    for (clients[0..interrupt_max_clients]) |*client| {
        if (client.socket == tcp.invalid_socket or client.protocol != .postgres or client.session.postgres.startup or client.closing or client.input_start != 0) continue;
        if (client.transport) |transport| {
            // During a query, drive only TLS and control traffic. Leave an
            // ordinary decrypted startup packet for the reactor to dispatch.
            if (client.input_len < 16) {
                if (transport.receive(client.socket) catch { client.close(); continue; }) client.touched = now;
                client.pullTls(transport) catch { client.close(); continue; };
                client.flush() catch { client.close(); continue; };
            }
            if (!transport.established()) continue;
        } else if (client.input_len < 16) {
            // Startup connections have not consumed any input yet.
            const n = tcp.recv(client.socket, client.input[client.input_len..16]) catch continue orelse continue;
            client.input_len += n;
        }
        if (pg.parseCancel(client.input[0..client.input_len])) |key| {
            cancelBackend(key);
            client.close();
        }
    }
}

// Bounded connection setup shared by the reactor and interrupt polling. No SQL
// is dispatched here; a fresh connection owns no transaction or shared session.
// Only the acceptor writes this list. Worker retirement also passes through
// state 1, so that state alone cannot identify an acceptor-owned client.
const TlsAdmissions = [capacity]?i64;

fn traceTlsAdmission(message: []const u8) void {
    sig.io.File.stderr().writeStreamingAll(.{}, message) catch {};
    sig.io.File.stderr().writeStreamingAll(.{}, "\n") catch {};
}

fn acceptConnections(listener: tcp.Socket, protocol: Protocol, max_clients: usize, limit: usize, admissions: ?*TlsAdmissions) !void {
    for (0..limit) |_| {
        const socket = tcp.accept(listener) catch break orelse break;
        if (protocol == .postgres and tls_slots.len == 0) {
            if (!queueOverflowCancel(socket)) tcp.close(socket);
            continue;
        }
        var free: ?usize = null;
        for (clients[0..max_clients], 0..) |*candidate, index| if (candidate.state.load(.acquire) == 0) {
            free = index;
            break;
        };
        const slot = free orelse {
            if (admissions) |pending| {
                var states: [capacity]u8 = undefined;
                for (clients[0..max_clients], pending[0..max_clients], states[0..max_clients]) |*candidate, deadline, *state| {
                    state.* = if (deadline != null) 'h' else @as(u8, @intCast(candidate.state.load(.acquire))) + '0';
                }
                traceTlsAdmission("TLS admission rejected because no slot was free");
                traceTlsAdmission(states[0..max_clients]);
            }
            // A saturated TLS listener closes without plaintext PG or TLS
            // negotiation. The plaintext overflow queue must never see it.
            if (tls_slots.len != 0 or protocol != .postgres or !queueOverflowCancel(socket)) tcp.close(socket);
            continue;
        };
        configureClient(socket, protocol, slot, &.{}, admissions != null) catch { tcp.close(socket); continue; };
        if (admissions) |pending| pending[slot] = monotonicClock() + admission_timeout_ns;
    }
}

fn configureClient(socket: tcp.Socket, protocol: Protocol, slot: usize, initial: []const u8, defer_ready: bool) !void {
    const client = &clients[slot];
    client.state.store(1, .release);
    client.cancel_key.store(0, .release);
    client.cancel_request.store(0, .release);
    client.socket = socket;
    client.timed_receive = .untested;
    client.result_region = .{};
    client.output_region = .{};
    client.startup_region = .{};
    client.transport = null;
    client.database_binding = null;
    client.notify_seen = 0;
    client.reader=if(parallel) &reactors[slot % reactor_count].reader else null;client.read_deadline=null;
    client.idle_transaction_deadline = null;
    client.idle_transaction_after_flush_ms = 0;
    client.output = &.{};
    client.blocked = false;
    client.connection = .{ .store = &store, .id = next_id };
    next_id +%= 1;
    if (next_id == 0) next_id = 1;
    // Each branch initializes the union in place (a switch expression would
    // build a full-size temporary per branch in Debug builds).
    switch (protocol) {
        .postgres => client.session = .{ .postgres = .{} },
        .mysql => client.session = .{ .mysql = .{} },
        .mssql => client.session = .{ .mssql = .{} },
    }
    client.protocol = protocol;
    // The accept/admission queue retains the socket on setup failure so it can
    // emit a PostgreSQL resource error instead of silently resetting TCP.
    errdefer {client.socket=tcp.invalid_socket;client.close();}
    try provisionClient(client);
    if (tls_slots.len != 0) client.transport = try tls_slots[slot].open(&tls_credentials);
    if (protocol == .postgres) {
        if (cluster_options.database_cluster) {
            try client.startup_region.reserve(&memory_ledger, pg.routed_startup_capacity);
            try client.session.postgres.configureStartupAdmission(.{
                .context = @ptrCast(client), .scratch = client.startup_region.memory.bytes,
                .admit = &clusterAdmit, .utility = &clusterCommand,
            });
        }
        // BackendKeyData: pid = slot + 1, fresh 32-bit secret.
        var secret: [4]u8 = undefined;
        try entropy.fill(&secret);
        client.cancel_secret = std.mem.readInt(u32, &secret, .little);
        client.session.postgres.configureBackendKey(@intCast(slot + 1), client.cancel_secret);
        if (catalog_credentials != null or verifier != null) {
            var random: [24]u8 = undefined;
            try entropy.fill(&random);
            var nonce: [32]u8 = undefined;
            _ = std.base64.standard.Encoder.encode(&nonce, &random);
            if (catalog_credentials) |*credentials| {
                try client.session.postgres.configureRoleCredentials(credentials, &nonce);
            } else if (verifier) |*credential| {
                if (bind_catalog_role) {
                    try client.session.postgres.configureRoleAuthentication(credential, &nonce);
                } else {
                    try client.session.postgres.configureAuthentication(credential, &nonce);
                }
            }
        }
    }
    @memcpy(client.input[0..initial.len], initial);
    client.input_len = initial.len;
    client.input_start = 0;
    client.output_len = 0;
    client.output_sent = 0;
    client.closing = false;
    client.touched = sig.os.clockMonotonicNs();
    client.output_len = switch (client.session) {
        .postgres => 0,
        .mysql => |*session| try session.begin(client.output),
        .mssql => |*session| try session.begin(client.output),
    };
    if (!defer_ready) publishClient(client, slot);
}

fn publishClient(client: *Client, slot: usize) void {
    if (client.protocol == .postgres) client.cancel_key.store((@as(u64, client.cancel_secret) << 1) | 1, .release);
    client.state.store(2, .release);
    if (parallel) reactors[slot % reactor_count].signal.notify();
}

// Perform only transport negotiation and inspect the first untyped PG frame.
// SQL and authentication run on the assigned reactor. An executing reactor
// cannot delay a separate encrypted cancel connection owned by the acceptor.
fn driveTlsAdmission(client: *Client, slot: usize, deadline: *?i64) !void {
    const transport = client.transport.?;
    if (!client.closing) {
        try client.pullTls(transport);
        if (transport.established()) {
            switch (pg.classifyStartup(client.input[0..client.input_len])) {
                .incomplete => if (transport.peer_closed) return error.EndOfStream,
                .startup => {
                    deadline.* = null;
                    publishClient(client, slot);
                    return;
                },
                .cancel => |key| {
                    if (client.input_len != 16) return error.InvalidStartup;
                    cancelBackend(key);
                    client.closing = true;
                },
                else => return error.InvalidStartup,
            }
        }
    }
    try client.flush();
    if (client.socket == tcp.invalid_socket) deadline.* = null;
}

const max_reactors = capacity;
var reactor_count: usize = 0;
var reactor_stop: std.atomic.Value(bool) = .init(false);
/// Committed NOTIFY count as last read under the store lock (any reactor).
var notification_sequence: std.atomic.Value(u64) = .init(0);
var reactor_failed: std.atomic.Value(bool) = .init(false);
var reactors: [max_reactors]Reactor = @splat(.{});

fn validateWorkers(options: Options) !void {
    if (options.linux_direct_receive and
        (!tcp.timed_receive_available or options.protocol != .postgres or options.workers < 2))
        return error.InvalidServerOptions;
    if (options.workers == 0 or options.workers > max_reactors or options.workers > options.max_clients) return error.InvalidServerOptions;
    if (options.database_cluster and options.workers != 1) return error.InvalidServerOptions;
    if (options.read_cache_bytes != 0 and options.workers < 2) return error.InvalidServerOptions;
    if (options.workers > 1 and !threads.available) return error.WorkersUnsupportedOnPlatform;
}

fn validateClientLimit(options: Options) !void {
    if (options.max_clients == 0 or options.max_clients > capacity) return error.InvalidServerOptions;
    if (options.max_clients <= standard_capacity) return;
    const os = @import("builtin").os.tag;
    if (os != .linux and os != .windows and os != .macos) return error.InvalidServerOptions;
    if (options.protocol != .postgres or options.mysql_port != null or options.mssql_port != null or
        options.database_cluster or options.max_prepared_transactions != 0) return error.InvalidServerOptions;
}

fn waitServer(listener: tcp.Socket, sockets: []const tcp.Socket, read: []bool, write: []bool, timeout_ms: u32) !bool {
    if (comptime capacity <= standard_capacity) return tcp.waitMasked(listener, sockets, read, write, timeout_ms);
    if (sockets.len <= tcp.max_clients) return tcp.waitMasked(listener, sockets, read, write, timeout_ms);
    var scratch: [capacity + 3]tcp.WaitDescriptor = undefined;
    return tcp.waitMaskedWithScratch(listener, sockets, read, write, timeout_ms, &scratch);
}

const Reactor = struct {
    thread: threads.Thread = .{},
    signal: threads.Signal = .{},
    reader:Reader=.{},
    index: usize = 0,
    idle_ms: u32 = 30000,
    direct_receive: bool = false,

    fn entry(context: ?*anyopaque) void {
        const self: *Reactor = @ptrCast(@alignCast(context.?));
        self.run() catch {
            reactor_failed.store(true, .release);
            reactor_stop.store(true, .release);
            for (&reactors) |*reactor| reactor.signal.notify();
        };
    }

    fn run(self: *Reactor) !void {
        defer self.reader.close();
        while (!reactor_stop.load(.acquire)) {
            const epoch = self.signal.epoch.load(.acquire);
            var sockets: [capacity]tcp.Socket = undefined;
            var readable: [capacity]bool = undefined;
            var writable: [capacity]bool = undefined;
            var owners: [capacity]usize = undefined;
            var active: usize = 0;
            var buffered = false;
            var poll_timeout_ms: u32 = 10;
            const now = sig.os.clockMonotonicNs();
            // Shared store state, timers and cancellation are read only while
            // holding the same mutex used for execution and disconnection.
            var woken: [capacity]usize = undefined;
            var woken_count: usize = 0;
            var check_waiters = false;
            var slot = self.index;
            while (slot < clients.len) : (slot += reactor_count) {
                const client = &clients[slot];
                switch (client.state.load(.acquire)) {
                    2 => check_waiters = check_waiters or client.blocked or client.cancel_request.load(.acquire) != 0,
                    3 => client.state.store(0, .release),
                    else => {},
                }
            }
            // Runnable connections handle interrupts during dispatch. Only a
            // blocked connection or an already published cancel needs a pass
            // under the shared store lock before processing socket traffic.
            if (check_waiters and store_mutex.tryLock()) {
                store.now_ns = now;
                slot = self.index;
                while (slot < clients.len) : (slot += reactor_count) {
                    const client = &clients[slot];
                    if (client.state.load(.acquire) != 2) continue;
                    if (client.cancel_request.load(.acquire) != 0)
                        pollWorkerInterrupts(client, client.connection.id);
                    if (!client.blocked or !client.connection.wakeReady()) continue;
                    var position = woken_count;
                    while (position != 0 and clients[woken[position - 1]].connection.waitStart() > client.connection.waitStart()) : (position -= 1)
                        woken[position] = woken[position - 1];
                    woken[position] = slot;
                    woken_count += 1;
                }
                store_mutex.unlock();
            }
            for (woken[0..woken_count]) |index| {
                const client = &clients[index];
                client.blocked = false;
                if (client.serve()) buffered = true;
            }
            slot = self.index;
            while (slot < clients.len) : (slot += reactor_count) {
                const client = &clients[slot];
                if (client.state.load(.acquire) != 2) continue;
                client.expireSessionTimeout(now);
                if (client.state.load(.acquire) != 2) continue;
                if (client.blocked) client.touched = now;
                if (now-client.touched >= @as(i64,self.idle_ms)*1000000) { client.close(); continue; }
                if (client.serve()) buffered = true;
                if (client.state.load(.acquire) != 2) continue;
                if (client.sessionDeadline()) |due| poll_timeout_ms = deadlinePollMs(monotonicClock(), due, poll_timeout_ms);
                sockets[active] = client.socket;
                readable[active] = client.wantsRead();
                writable[active] = client.wantsWrite();
                owners[active] = slot;
                active += 1;
            }
            if (active == 0) { self.reader.close();self.signal.wait(epoch, 100); continue; }
            if (self.direct_receive and active == 1 and readable[0] and !writable[0] and
                !buffered and !check_waiters and self.signal.epoch.load(.acquire) == epoch and
                clients[owners[0]].directReceive()) continue;
            // Finite poll bounds new admission, cancellation and shutdown
            // latency without introducing a second socket wakeup protocol.
            _ = try waitServer(tcp.invalid_socket, sockets[0..active], readable[0..active], writable[0..active], if (buffered) 0 else poll_timeout_ms);
            // A reactor owns these slots through the wait. Newly admitted
            // clients have no readiness result until the next iteration.
            for (owners[0..active], readable[0..active], writable[0..active]) |owner, can_read, can_write| {
                const client = &clients[owner];
                if (can_write) client.flush() catch { client.close(); continue; };
                if (client.state.load(.acquire) != 2) continue;
                if (can_read) client.receiveReady(false) catch client.close();
            }
        }
    }
};

fn runParallel(options: Options, listeners: []const tcp.Socket, protocols: []const Protocol) !void {
    reactor_count = options.workers;
    reactor_stop.store(false, .release); reactor_failed.store(false, .release);
    var started: usize = 0;
    defer {
        reactor_stop.store(true, .release);
        for (reactors[0..started]) |*reactor| reactor.signal.notify();
        for (reactors[0..started]) |*reactor| reactor.thread.join() catch @panic("cannot release live reactor ownership");
    }
    for (reactors[0..reactor_count], 0..) |*reactor, index| {
        reactor.* = .{ .index = index, .idle_ms = options.idle_ms, .direct_receive = options.linux_direct_receive };
    }
    for (reactors[0..reactor_count]) |*reactor| {
        try reactor.thread.start(&Reactor.entry, reactor);
        started += 1;
    }
    if (tls_slots.len != 0) return runTlsAcceptor(listeners[0], options.max_clients);
    while (!reactor_stop.load(.acquire)) {
        for (listeners,protocols) |listener,protocol| try acceptConnections(listener,protocol,options.max_clients,capacity,null);
        pollOverflowCancels(sig.os.clockMonotonicNs());
        var sockets: [2]tcp.Socket = @splat(tcp.invalid_socket);
        var readable: [2]bool = @splat(false);
        var writable: [2]bool = @splat(false);
        for (1..listeners.len) |index| { sockets[index-1]=listeners[index]; readable[index-1]=true; }
        _ = try tcp.waitMasked(listeners[0],&sockets,&readable,&writable,10);
    }
    if (reactor_failed.load(.acquire)) return error.ReactorFailed;
}

fn runTlsAcceptor(listener: tcp.Socket, max_clients: usize) !void {
    var pending: TlsAdmissions = @splat(null);
    while (!reactor_stop.load(.acquire)) {
        try acceptConnections(listener, .postgres, max_clients, capacity, &pending);
        var sockets: [capacity]tcp.Socket = @splat(tcp.invalid_socket);
        var readable: [capacity]bool = @splat(false);
        var writable: [capacity]bool = @splat(false);
        const now = monotonicClock();
        var buffered = false;
        for (pending[0..max_clients], 0..) |*deadline, slot| {
            const due = deadline.* orelse continue;
            const client = &clients[slot];
            if (now >= due) {
                client.close();
                deadline.* = null;
                continue;
            }
            driveTlsAdmission(client, slot, deadline) catch |err| {
                traceTlsAdmission(@errorName(err));
                client.close();
                deadline.* = null;
                continue;
            };
            if (deadline.* == null) continue;
            sockets[slot] = client.socket;
            readable[slot] = client.wantsRead();
            writable[slot] = client.wantsWrite();
            const transport = client.transport.?;
            if (transport.more and !transport.needsWrite()) buffered = true;
        }
        _ = try waitServer(listener, sockets[0..max_clients], readable[0..max_clients], writable[0..max_clients], if (buffered) 0 else 10);
        for (pending[0..max_clients], 0..) |*deadline, slot| {
            if (deadline.* == null) continue;
            const client = &clients[slot];
            if (writable[slot]) client.flush() catch client.close();
            if (client.socket != tcp.invalid_socket and readable[slot]) client.receive(false) catch |err| {
                traceTlsAdmission(@errorName(err));
                client.close();
            };
            if (client.socket == tcp.invalid_socket) deadline.* = null;
        }
    }
    if (reactor_failed.load(.acquire)) return error.ReactorFailed;
}

const ReadCachePlan = struct {
    bytes: usize = 0,
    region_bytes: u64 = 0,
    entries: usize = 0,
    rows: usize = 0,
    small_offset: usize = 0,
    small_bytes: usize = 0,
    projected_bytes: usize = 0,
    projected_entries: usize = 0,
    projected_rows: usize = 0,
};
const read_cache_key_bytes = 1024;
const projected_cache_min_scan_rows = 65536;

fn planReadCache(options: Options, has_large_projection: bool) !ReadCachePlan {
    if (options.read_cache_bytes == 0) return .{};
    if (options.protocol != .postgres or options.database_cluster or options.workers == 1) return error.InvalidServerOptions;
    const rows = @min(@as(usize, 128), options.result_rows);
    const projected_rows = if (has_large_projection) @min(@as(usize, 2048), options.result_rows) else 0;
    const layout = Raw.ReadResultCache.tieredLayoutForBytes(options.read_cache_bytes, read_cache_key_bytes, rows, projected_rows) catch return error.InvalidServerOptions;
    const charges = machine_capacity.startupCharges(&.{layout.bytes}, 0) catch return error.InvalidServerOptions;
    const projected_bytes = if (layout.projected) |medium| medium.bytes else 0;
    const projected_entries = if (layout.projected) |medium| medium.entries else 0;
    return .{
        .bytes=layout.bytes, .region_bytes=charges.rounded, .entries=layout.small.entries, .rows=rows,
        .small_offset=layout.small_offset, .small_bytes=layout.small.bytes,
        .projected_bytes=projected_bytes, .projected_entries=projected_entries,
        .projected_rows=if (projected_entries == 0) 0 else projected_rows,
    };
}

const StoragePlan = struct {
    max_rows: usize,
    versions: usize,
    budgets: [profile.tables]Raw.TableBudget,
    refs: usize,
    bytes: usize,
    workspace_bytes: usize,
    result_bytes: usize,
    response_bytes: usize,
    read_cache: ReadCachePlan,
};

fn storagePlan(options: Options) !StoragePlan {
    if ((options.checkpoint_stem == null) != (options.checkpoint_id == null) or
        (options.checkpoint_every != 0 and options.checkpoint_stem == null)) return error.InvalidServerOptions;
    if (options.checkpoint_stem != null and (options.memory or options.create_data or options.database_cluster or
        options.protocol != .postgres or options.mysql_port != null or options.mssql_port != null or
        options.max_prepared_transactions != 0)) return error.InvalidServerOptions;
    if ((options.tls_certificate_file == null) != (options.tls_key_file == null)) return error.InvalidServerOptions;
    if (options.tls_certificate_file != null and (options.protocol != .postgres or options.database_cluster or
        options.mysql_port != null or options.mssql_port != null or options.insecure_local or
        options.read_cache_bytes != 0 or
        (options.auth_verifier_file == null and options.auth_credentials_file == null))) return error.InvalidServerOptions;
    if (options.database_cluster and (options.memory or options.protocol != .postgres or
        options.mysql_port != null or options.mssql_port != null or options.auth_catalog_role or options.auth_credentials_file != null)) return error.InvalidServerOptions;
    if (options.max_prepared_transactions >= Store.prepared_capacity or (options.memory and options.max_prepared_transactions != 0)) return error.InvalidServerOptions;
    if (!profile.integral_version_banks and options.table_integral_words != null) return error.InvalidServerOptions;
    if (options.typed_bank_memory_profile and options.table_integral_words == null) return error.InvalidServerOptions;
    if (options.query_refs) |refs| {
        if (!options.typed_bank_memory_profile or refs == 0 or refs > 0x7fffffff) return error.InvalidServerOptions;
    }
    if (options.storage_rows == 0 or options.storage_rows > 0x3fffffff) return error.InvalidServerOptions;
    var budgets: [profile.tables]Raw.TableBudget = undefined;
    var max_rows: usize = 0; var max_versions: usize = 0;
    var typed_banks = false;
    var has_large_projection = false;
    for (&budgets,0..) |*budget,index| {
        const rows = if (options.table_rows) |values| values[index] else options.storage_rows;
        if (rows == 0 or rows > 0x3fffffff) return error.InvalidServerOptions;
        const versions = if (options.table_versions) |values| values[index] else options.storage_versions orelse if (rows == profile.rows) profile.versions else rows * 2;
        if (versions <= rows or versions > 0x7fffffff) return error.InvalidServerOptions;
        budget.* = .{ .rows=rows, .versions=versions };
        if (comptime profile.integral_version_banks) {
            if (options.table_integral_words) |words| budget.integral_row_words = words[index];
        }
        if (budget.integral_row_words != 0) {
            typed_banks = true;
            if (options.typed_bank_memory_profile) {
                budget.omit_compact_scan = true;
                budget.secondary_hashes = 0;
            }
        }
        if (!budget.omit_compact_scan and rows >= projected_cache_min_scan_rows) has_large_projection = true;
        max_rows=@max(max_rows,rows); max_versions=@max(max_versions,versions);
    }
    if (options.typed_bank_memory_profile and !typed_banks) return error.InvalidServerOptions;
    const workspace_refs = Raw.queryReferenceCount(options.work_rows) catch return error.InvalidServerOptions;
    const ref_count = if (options.typed_bank_memory_profile) options.query_refs orelse workspace_refs else @max(max_rows * 4, workspace_refs);
    return .{ .max_rows=max_rows, .versions = max_versions, .budgets=budgets, .refs = ref_count, .bytes = try Raw.backingBytesFor(budgets, ref_count),
        .workspace_bytes = Raw.queryWorkspaceBytes(options.work_rows) catch return error.InvalidServerOptions,
        .result_bytes = Raw.Result.rowBytes(options.result_rows) catch return error.InvalidServerOptions,
        .response_bytes = responseBytes(options.result_rows) catch return error.InvalidServerOptions,
        .read_cache = try planReadCache(options, has_large_projection),
    };
}

fn readContextPayload(options: Options, plan: *const StoragePlan) !usize {
    const read_payload=@addWithOverflow(try Raw.ReadContext.backingBytesFor(options.work_rows,plan.refs),@sizeOf(Raw.ReadContext));
    if(read_payload[1]!=0) return error.InvalidServerOptions;
    return read_payload[0];
}

fn writeStorageDetails(out: sig.io.File, options: Options, plan: *const StoragePlan, read_payload: usize) !void {
    var buffer: [512]u8 = undefined;
    try out.writeStreamingAll(.{}, try std.fmt.bufPrint(&buffer,
        ",\"typed_bank_memory_profile\":{s},\"query_refs\":{d},\"query_reference_bytes\":{d},\"read_context_payload_bytes_per_worker\":{d},\"max_prepared_transactions\":{d},\"prepared_transactions_ceiling\":{d},\"database_cluster\":{s},\"cluster_database_ceiling\":{d},\"compact_version_stamps\":{s},\"storage_version_bytes\":{d},\"table_storage\":[",
        .{ if (options.typed_bank_memory_profile) "true" else "false", plan.refs, plan.refs * @sizeOf(u32), read_payload, options.max_prepared_transactions, Store.prepared_capacity - 1, if (options.database_cluster) "true" else "false", cluster_capacity, if (profile.compact_version_stamps) "true" else "false", @sizeOf(Raw.StorageVersion) }));
    for (plan.budgets, 0..) |budget, index| {
        if (index != 0) try out.writeStreamingAll(.{}, ",");
        const scan = config.compact_integral_scans and config.columns <= 64 and !budget.omit_compact_scan;
        try out.writeStreamingAll(.{}, try std.fmt.bufPrint(&buffer,
            "{{\"rows\":{d},\"versions\":{d},\"integral_row_words\":{d},\"committed_scan_projection\":{s},\"secondary_hashes\":{d}}}",
            .{ budget.rows, budget.versions, budget.integral_row_words, if (scan) "true" else "false", budget.secondary_hashes orelse config.external_indexes }));
    }
    try out.writeStreamingAll(.{}, try std.fmt.bufPrint(&buffer,
        "],\"read_cache_bytes\":{d},\"read_cache_region_bytes\":{d},\"read_cache_entries\":{d},\"read_cache_rows_per_entry\":{d}," ++
        "\"read_cache_projected_bytes\":{d},\"read_cache_projected_entries\":{d},\"read_cache_projected_rows_per_entry\":{d}}}\n",
        .{ plan.read_cache.bytes, plan.read_cache.region_bytes, plan.read_cache.entries, plan.read_cache.rows,
            plan.read_cache.projected_bytes, plan.read_cache.projected_entries, plan.read_cache.projected_rows }));
}

fn storageCharges(options: Options, plan: *const StoragePlan, worker_bytes: u64) !machine_capacity.StartupCharges {
    var regions = [_]usize{ plan.bytes, plan.workspace_bytes, options.max_clients * @sizeOf(Client), 0, 0 };
    var count: usize = 3;
    if (plan.read_cache.bytes != 0) {
        regions[count] = plan.read_cache.bytes;
        count += 1;
    }
    if (options.tls_certificate_file != null) {
        regions[count] = options.max_clients * @sizeOf(postgres_tls.Slot);
        count += 1;
    }
    return machine_capacity.startupCharges(regions[0..count], worker_bytes);
}

/// Bound the existing lazily mapped arenas and fixed hosted state for expanded
/// admission. The conservative static allowance can include resident pages.
fn expandedClientCharge(options: Options, plan: *const StoragePlan, startup: machine_capacity.StartupCharges) !u64 {
    const client = try machine_capacity.startupCharges(&.{ plan.result_bytes, plan.response_bytes }, 0);
    const client_total = try std.math.mul(u64, client.rounded, options.max_clients);
    const worker_total = if (options.workers <= 1) 0 else blk: {
        const scratch = try Raw.ReadContext.backingBytesFor(options.work_rows, plan.refs);
        const worker = try machine_capacity.startupCharges(&.{ @sizeOf(Raw.ReadContext), scratch }, 0);
        break :blk try std.math.mul(u64, worker.rounded, options.workers);
    };
    const fixed = try machine_capacity.startupCharges(&.{
        @sizeOf(Store), @sizeOf(Persistence), @sizeOf(@TypeOf(recovery_buffer)), @sizeOf(@TypeOf(overflow_cancels)),
        @sizeOf(@TypeOf(reactors)), @sizeOf(@TypeOf(memory_slots)),
    }, 0);
    const live_regions = try std.math.add(u64, client_total, worker_total);
    return std.math.add(u64, try std.math.add(u64, startup.rounded, fixed.rounded), live_regions);
}

fn chooseWorkers(options: Options, plan: *const StoragePlan, observed: machine_capacity.Sample) !usize {
    if (options.workers != 0) return options.workers;
    if (!threads.available or options.protocol != .postgres or options.database_cluster or options.tls_certificate_file != null) return 1;
    const max_cpu = @min(options.max_clients, max_reactors, @as(usize, observed.plan.cpu_workers));
    if (max_cpu <= 1) return 1;
    const fixed = try storageCharges(options, plan, 0);
    const read_bytes = try Raw.ReadContext.backingBytesFor(options.work_rows, plan.refs);
    const per_worker = try machine_capacity.startupCharges(
        &.{ @sizeOf(Raw.ReadContext), read_bytes, plan.result_bytes, plan.response_bytes }, worker_budget_each);
    const available = observed.new_region_bytes -| fixed.rounded;
    return @intCast(@max(1, @min(@as(u64, max_cpu), available / per_worker.rounded)));
}

pub fn describe(requested: Options) !void {
    var options = requested;
    try validateClientLimit(options);
    if (options.idle_ms == 0) return error.InvalidServerOptions;
    const plan = try storagePlan(options);
    if (options.workers == 0) options.workers = if (!threads.available or options.protocol != .postgres or options.max_clients == 1) 1
        else try chooseWorkers(options, &plan, try machine_capacity.sample(options.memory_budget, options.os_headroom, 0));
    try validateWorkers(options);
    const read_payload = try readContextPayload(options, &plan);
    var buffer: [4096]u8 = undefined;
    const message = try sig.fmt.bufPrint(&buffer,
        "{{\"schema\":1,\"version\":\"{s}\",\"release_stage\":\"beta\",\"production_ready\":false,\"default_protocol\":\"postgres\",\"bind\":\"127.0.0.1\",\"tls\":{s},\"roles\":false,\"isolation\":\"read_committed\",\"workers\":{d},\"sql_execution_workers\":{d},\"exclusive_sql_workers\":1,\"parallel_simple_reads\":{s},\"native_workers_available\":{s},\"libc_linked\":{s},\"cpu_model\":\"{s}\",\"build_mode\":\"{s}\",\"rows_per_table\":{d},\"versions_per_table\":{d},\"tables\":{d},\"columns\":{d},\"projection_columns\":{d},\"result_rows\":{d},\"work_rows\":{d},\"text_bytes\":{d},\"name_bytes\":{d},\"clients\":{d},\"compiled_clients\":{d},\"transaction_bytes\":{d},\"tx_writes\":{d},\"row_locks\":{d},\"predicates\":{d},\"expr_nodes\":{d},\"expr_depth\":{d},\"relations\":{d},\"subqueries\":{d},\"ctes\":{d},\"savepoints\":{d},\"sequences\":{d},\"numeric_digits\":{d},\"json_depth\":{d},\"copy_line_bytes\":{d},\"pg_packet_bytes\":{d},\"pg_prepared_sql_bytes\":{d},\"pg_statements\":{d},\"pg_portals\":{d},\"pg_parameters\":{d},\"input_bytes\":{d},\"output_bytes_per_client\":{d},\"backing_bytes\":{d},\"query_workspace_bytes\":{d},\"result_bytes_per_client\":{d},\"client_metadata_bytes\":{d},\"summary_groups\":{d},\"compact_integral_scans\":{s},\"wal_checkpointing\":false,\"mysql_tds_authentication\":false}}\n",
        .{ @import("ramsql").version, if (options.tls_certificate_file == null) "false" else "true", options.workers, options.workers, if(options.workers>1) "true" else "false", if (threads.available) "true" else "false", if (@import("builtin").link_libc) "true" else "false", @import("builtin").cpu.model.name, @tagName(@import("builtin").mode), plan.max_rows, plan.versions, config.tables, config.columns, config.projection_columns, options.result_rows, options.work_rows, config.text_bytes, config.name_bytes, options.max_clients, capacity, profile.transaction_bytes, config.tx_writes, config.row_locks, config.predicates, config.expr_nodes, config.expr_depth, config.relations, config.subqueries, config.ctes, config.savepoints, config.sequences, config.numeric_digits, config.json_depth, config.copy_line_bytes, pg.max_packet, pg.sql_capacity, pg.statement_capacity, pg.portal_capacity, pg.parameter_capacity, input_capacity, plan.response_bytes, plan.bytes, plan.workspace_bytes, plan.result_bytes, options.max_clients * @sizeOf(Client), config.summary_groups, if(config.compact_integral_scans and config.columns <= 64) "true" else "false" });
    const out = sig.io.File.stdout();
    try out.writeStreamingAll(.{}, message[0..message.len-2]);
    var number: [32]u8 = undefined;
    try out.writeStreamingAll(.{}, ",\"postgres_tls_bytes_per_client\":");
    try out.writeStreamingAll(.{}, try sig.fmt.bufPrint(&number, "{d}", .{if (options.tls_certificate_file == null) @as(usize, 0) else @sizeOf(postgres_tls.Slot)}));
    try out.writeStreamingAll(.{}, ",\"postgres_scram_catalog_roles\":true,\"postgres_scram_credential_limit\":");
    try out.writeStreamingAll(.{}, try sig.fmt.bufPrint(&number, "{d}", .{config.catalog_objects}));
    try out.writeStreamingAll(.{}, ",\"table_rows\":[");
    for (plan.budgets,0..) |budget,index| {
        if (index!=0) try out.writeStreamingAll(.{},",");
        try out.writeStreamingAll(.{},try sig.fmt.bufPrint(&number,"{d}",.{budget.rows}));
    }
    try out.writeStreamingAll(.{},"],\"table_versions\":[");
    for (plan.budgets,0..) |budget,index| {
        if (index!=0) try out.writeStreamingAll(.{},",");
        try out.writeStreamingAll(.{},try sig.fmt.bufPrint(&number,"{d}",.{budget.versions}));
    }
    try out.writeStreamingAll(.{},"],\"table_integral_words\":[");
    for (0..profile.tables) |index| {
        if (index!=0) try out.writeStreamingAll(.{},",");
        const words = if (options.table_integral_words) |values| values[index] else 0;
        try out.writeStreamingAll(.{},try sig.fmt.bufPrint(&number,"{d}",.{words}));
    }
    var policy_buffer:[384]u8=undefined;
    try out.writeStreamingAll(.{},try std.fmt.bufPrint(&policy_buffer,
        "],\"integral_version_banks\":{s},\"server_region_budgeting\":true,\"memory_budget_bytes\":{?d},\"os_headroom_bytes\":{?d},\"parallel_extended_reads\":{s}",
        .{if (profile.integral_version_banks) "true" else "false",options.memory_budget,options.os_headroom,if(options.workers>1) "true" else "false"}));
    try writeStorageDetails(out, options, &plan, read_payload);
}

pub fn describeMachine(requested:Options) !void {
    var options = requested;
    try validateClientLimit(options);
    const plan=try storagePlan(options);
    const observed = try machine_capacity.sample(options.memory_budget, options.os_headroom, 0);
    options.workers = try chooseWorkers(options, &plan, observed);
    try validateWorkers(options);
    const read_payload = try readContextPayload(options, &plan);
    const worker_bytes=if(options.workers>1) options.workers*worker_budget_each else 0;
    const startup=try storageCharges(options, &plan, worker_bytes);
    var buffer: [4096]u8 = undefined;
    const machine = try machine_capacity.describe(&buffer, observed, startup, options.workers);
    var layout: [192]u8 = undefined;
    const memory = try std.fmt.bufPrint(&layout, ",\"backing_bytes\":{d},\"query_workspace_bytes\":{d},\"client_metadata_bytes\":{d}",
        .{ plan.bytes, plan.workspace_bytes, options.max_clients * @sizeOf(Client) });
    const out = sig.io.File.stdout();
    try out.writeStreamingAll(.{}, machine[0..machine.len - 2]);
    try out.writeStreamingAll(.{}, memory);
    if (options.max_clients > standard_capacity) {
        const charge = try expandedClientCharge(options, &plan, startup);
        var expanded: [192]u8 = undefined;
        try out.writeStreamingAll(.{}, try std.fmt.bufPrint(&expanded,
            ",\"expanded_client_peak_bytes\":{d},\"expanded_client_peak_fits\":{s},\"transaction_slots\":{d}",
            .{ charge, if (charge <= observed.new_region_bytes) "true" else "false", profile.transactions }));
    }
    try writeStorageDetails(out, options, &plan, read_payload);
}
/// Absolute spelling of a data path for operator messages; the file need not
/// exist. Falls back to `path` itself when it cannot be resolved into `out`.
fn absolutePath(path: []const u8, out: *[4096]u8) []const u8 {
    const target = @import("builtin");
    if (target.os.tag == .windows) {
        var wide: [4096]u16 = undefined;
        var full: [4096]u16 = undefined;
        if (path.len >= wide.len) return path;
        const len = std.unicode.utf8ToUtf16Le(&wide, path) catch return path;
        wide[len] = 0;
        const bytes = std.os.windows.ntdll.RtlGetFullPathName_U(wide[0..len :0].ptr, full.len * 2, &full, null);
        if (bytes == 0 or bytes >= full.len * 2) return path;
        return out[0 .. sig.os.wideToUtf8(full[0 .. bytes / 2], out) orelse return path];
    }
    if (path.len != 0 and path[0] == '/') return path;
    var cwd: [4096]u8 = undefined;
    const cwd_len = if (target.os.tag == .linux and !target.link_libc) blk: {
        if (std.os.linux.errno(std.os.linux.getcwd(&cwd, cwd.len)) != .SUCCESS) return path;
        break :blk std.mem.indexOfScalar(u8, &cwd, 0) orelse return path;
    } else (sig.process.getCwd(&cwd) catch return path).len;
    return sig.fs.joinPath(out, &.{ cwd[0..cwd_len], path }) catch path;
}

fn dataNotice(path: []const u8, state: []const u8) void {
    const stderr = sig.io.File.stderr();
    stderr.writeStreamingAll(.{}, "ramsql: data file ") catch {};
    stderr.writeStreamingAll(.{}, path) catch {};
    stderr.writeStreamingAll(.{}, state) catch {};
}

/// Recovers the durable data file before any listener exists and reports its
/// absolute path. A wrong working directory or a mistyped path never becomes a
/// new, empty authoritative database: the existing-file open runs first, and
/// only its FileNotFound plus `create_data` creates. Without `create_data` that
/// is DataFileMissing and nothing is created; other open failures propagate.
fn checkpointIdentity(options: Options) @import("persistence.sig").CheckpointIdentity {
    return .{ .database = options.checkpoint_id.?, .namespace = "ramsql-checkpt-1".*, .oid = store.database_oid };
}

fn openData(options: Options) !void {
    if (options.checkpoint_stem) |stem| {
        _ = try persistence.initCheckpoint(stem, checkpointIdentity(options), &store, &recovery_buffer);
        checkpoint_every = options.checkpoint_every;
        last_checkpoint_attempt = persistence.sequence;
        dataNotice(stem, " (checkpoint generation opened)\n");
        return;
    }
    var path_buffer: [4096]u8 = undefined;
    const data_path = absolutePath(options.data, &path_buffer);
    if (persistence.initExisting(options.data, &store, &recovery_buffer)) |_| {
        dataNotice(data_path, " (opened)\n");
    } else |err| {
        if (err != error.FileNotFound) return err;
        if (!options.create_data) {
            dataNotice(data_path, " does not exist; pass --create-data to create a new database\n");
            return error.DataFileMissing;
        }
        _ = try persistence.init(options.data, &store, &recovery_buffer);
        dataNotice(data_path, " (created)\n");
    }
}

fn openStorage(options: Options,backing:*owned_pages.Region) !void {
    const plan = try storagePlan(options);
    try backing.reserve(&memory_ledger,plan.bytes);
    errdefer backing.close() catch @panic("lost failed storage ownership");
    try store.database.bindMemoryFor(backing.memory.bytes, plan.budgets, plan.refs);
}

fn clusterWalPath(oid: u32, buffer: []u8) ![]const u8 {
    return sig.fmt.bufPrint(buffer, "{s}.db-{d}.wal", .{ cluster_options.data, oid });
}

fn clusterStoreOpen(id: ClusterRegistry.Id, oid: u32, name: []const u8, create: bool) !*Store {
    var path_buffer: [4096]u8 = undefined;
    const path = try clusterWalPath(oid, &path_buffer);
    const identity: @import("persistence.sig").DatabaseIdentity = .{ .cluster = cluster_catalog.identity orelse return error.InvalidDatabaseCatalog, .oid = oid };
    if (std.mem.eql(u8, name, primary_database)) {
        store.database_oid = oid;
        _ = try persistence.initDatabase(path, &store, &recovery_buffer, identity, create);
        return &store;
    }
    const slot = &cluster_slots[id.index];
    const plan = try storagePlan(cluster_options);
    try refreshMemoryBudget();
    errdefer slot.close();
    try slot.metadata.reserve(&memory_ledger, @sizeOf(ClusterStore));
    const objects = try slot.metadata.memory.view(ClusterStore, 0, 1);
    const database = &objects[0];
    database.* = .{};
    slot.database = database;
    try slot.backing.reserve(&memory_ledger, plan.bytes);
    try database.store.database.bindMemoryFor(slot.backing.memory.bytes, plan.budgets, plan.refs);
    try slot.workspace.reserve(&memory_ledger, plan.workspace_bytes);
    try database.store.database.bindWorkspaceMemory(slot.workspace.memory.bytes, cluster_options.work_rows);
    try slot.log_buffer.reserve(&memory_ledger, profile.transaction_bytes);
    configureStore(&database.store, cluster_options);
    database.store.database_oid = oid;
    _ = try database.persistence.initDatabase(path, &database.store, slot.log_buffer.memory.bytes[0..profile.transaction_bytes], identity, create);
    return &database.store;
}

fn createClusterDatabase(name: []const u8) !void {
    const oid = std.math.add(u32, @max(@as(u32, 4), cluster_catalog.high_water), 1) catch return error.DatabaseCapacity;
    const id = try cluster_registry.reserve(name, oid, cluster_owner);
    errdefer _ = cluster_registry.abortReservation(id) catch @panic("lost database reservation");
    try cluster_catalog.reserve(oid);
    const opened = try clusterStoreOpen(id, oid, name, true);
    errdefer {
        if (opened == &store) persistence.close() else cluster_slots[id.index].close();
    }
    try cluster_registry.stageStore(id, opened);
    try cluster_catalog.publish(oid, cluster_owner, name);
    cluster_registry.publish(id) catch @panic("durable database publication lost");
}

fn openCluster() !void {
    var path_buffer: [4096]u8 = undefined;
    const path = try sig.fmt.bufPrint(&path_buffer, "{s}.cluster", .{cluster_options.data});
    cluster_registry = ClusterRegistry.init(&cluster_entries, &cluster_leases);
    try cluster_catalog.open(path, cluster_options.create_data);
    if (cluster_catalog.identity == null) {
        if (!cluster_options.create_data) return error.InvalidDatabaseCatalog;
        var identity: [16]u8 = undefined;
        try entropy.fill(&identity);
        try cluster_catalog.initializeIdentity(identity);
    }
    var previous: u32 = 0;
    while (true) {
        var next: ?*const ClusterCatalog.Entry = null;
        for (&cluster_catalog.entries) |*entry| {
            if (entry.oid > previous and (next == null or entry.oid < next.?.oid)) next = entry;
        }
        const entry = next orelse break;
        if (entry.owner != cluster_owner) return error.InvalidDatabaseCatalog;
        const id = try cluster_registry.reserve(entry.slice(), entry.oid, entry.owner);
        try cluster_registry.stageStore(id, try clusterStoreOpen(id, entry.oid, entry.slice(), false));
        try cluster_registry.publish(id);
        previous = entry.oid;
    }
    cluster_registry.raiseOidHighWater(cluster_catalog.high_water);
    if (cluster_registry.lookup(primary_database) == null) {
        if (!cluster_options.create_data or previous != 0) return error.InvalidDatabaseCatalog;
        try createClusterDatabase(primary_database);
    }
    try validateClusterPrepared();
}

fn validateClusterPrepared() !void {
    var count: usize = 0;
    for (0..cluster_capacity) |index| {
        const current = cluster_registry.readyStoreAt(index) orelse continue;
        count += current.preparedCount();
        for (&current.prepared) |*entry| {
            if (!entry.used) continue;
            for (0..index) |earlier| {
                const peer = cluster_registry.readyStoreAt(earlier) orelse continue;
                if (peer.hasPreparedGid(&entry.metadata.gid)) return error.ChangeRecordConflict;
            }
        }
    }
    if (count > cluster_options.max_prepared_transactions) return error.TransactionCapacity;
}

fn checkClusterPrepared(current: *const Store, gid: *const Raw.PreparedWal.Gid, operation: Store.PreparedOperation) anyerror!void {
    if (cluster_catalog.failed) return error.WriterPoisoned;
    var count: usize = 0;
    for (0..cluster_capacity) |index| {
        const peer = cluster_registry.readyStoreAt(index) orelse continue;
        // An uncertain prepare sync can still be present in a peer's WAL.
        if (peer.storage_failed) return error.WriterPoisoned;
        if (peer != current and peer.hasPreparedGid(gid)) {
            return if (operation == .prepare) error.DuplicateObject else error.UnsupportedStatement;
        }
        count += peer.preparedCount();
    }
    if (operation == .prepare and count >= current.prepared_limit) return error.TransactionCapacity;
}

fn clusterAcl(_: ?*anyopaque, principal: u32, metadata: ClusterRegistry.Metadata) bool {
    return principal == metadata.owner;
}

fn databaseCatalogRow(_: ?*const anyopaque, index: usize) ?sql.DatabaseCatalogRow {
    const entry = cluster_registry.metadataAt(index) orelse return null;
    return .{ .oid = entry.oid, .owner = entry.owner, .name = entry.name };
}

fn standaloneDatabaseCatalogRow(_: ?*const anyopaque, index: usize) ?sql.DatabaseCatalogRow {
    if (index != 0) return null;
    return .{ .oid = store.database_oid, .owner = cluster_owner, .name = primary_database };
}

fn clusterAdmit(context: *anyopaque, connection: *DB, request: pg.StartupAdmission) anyerror!void {
    const client: *Client = @ptrCast(@alignCast(context));
    if (cluster_catalog.failed) return error.WriterPoisoned;
    if (!std.mem.eql(u8, request.user, cluster_options.auth_user orelse "postgres")) return error.AdmissionDenied;
    if (request.method == .trusted and !cluster_options.insecure_local) return error.AdmissionDenied;
    const binding = try cluster_registry.attach(request.database, .{ .principal = cluster_owner, .allowed = &clusterAcl });
    errdefer cluster_registry.release(binding.lease) catch @panic("lost provisional database lease");
    const former = connection.store;
    connection.store = binding.store;
    errdefer connection.store = former;
    var parameters = request.parameters();
    while (try parameters.next()) |param| {
        if (!std.mem.eql(u8, param.key, "database")) try connection.startupParameter(param.key, param.value);
    }
    try connection.startupParameter("database", request.database);
    client.database_binding = binding;
}

fn clusterCommand(context: *anyopaque, connection: *DB, source: []const u8) anyerror!?[]const u8 {
    const command = (try Raw.parseDatabaseCommand(source, .{})) orelse return null;
    const client: *Client = @ptrCast(@alignCast(context));
    const binding = client.database_binding orelse return error.AdmissionDenied;
    if (connection.engine_session.values.role != 0) return error.AdmissionDenied;
    if (connection.transactionStatus() != 'I') return error.ActiveSqlTransaction;
    if (cluster_catalog.failed) return error.WriterPoisoned;
    switch (command) {
        .create => |name| {
            try createClusterDatabase(name.slice());
            return "CREATE DATABASE";
        },
        .drop => |drop| {
            const target = cluster_registry.lookup(drop.name.slice()) orelse return if (drop.if_exists) "DROP DATABASE" else error.DatabaseNotFound;
            if (std.mem.eql(u8, target.name, primary_database) or target.id.index == binding.database.index) return error.DatabaseBusy;
            const slot = &cluster_slots[target.id.index];
            if (slot.database.?.store.storage_failed or slot.database.?.store.preparedCount() != 0) return error.DatabaseBusy;
            try cluster_registry.beginDrop(target.id);
            // On an uncertain sync outcome, keep the database fenced until
            // restart resolves the durable tombstone.
            try cluster_catalog.drop(target.oid);
            _ = cluster_registry.finishDrop(target.id) catch @panic("durable database tombstone lost");
            slot.close();
            var path_buffer: [4096]u8 = undefined;
            if (clusterWalPath(target.oid, &path_buffer)) |path| disk.remove(path) catch {} else |_| {}
            return "DROP DATABASE";
        },
    }
}

fn configureStore(target: *Store, options: Options) void {
    if (wall_clock.available) target.wall_clock = &wallClock;
    target.monotonic_clock = &monotonicClock;
    target.max_query_work = options.max_query_work;
    target.prepared_limit = options.max_prepared_transactions;
    target.interrupt_poll = if (parallel) &pollWorkerInterrupts else &pollInterrupts;
    target.zone_provider = .{ .context = @ptrCast(&zones), .lookup = &Zones.lookup };
    if (options.database_cluster) {
        target.prepared_check = &checkClusterPrepared;
        target.database.setDatabaseCatalog(.{ .capacity = cluster_capacity, .row = &databaseCatalogRow });
    } else {
        target.database.setDatabaseCatalog(.{ .capacity = 1, .row = &standaloneDatabaseCatalogRow, .use_session_database_name = true });
    }
}

fn readCredentialFile(path: []const u8, buffer: []u8) ![]u8 {
    const file = try sig.io.Dir.cwd().openFile(.{}, path, .{});
    defer file.close(.{});
    var reader = file.reader(.{}, &.{});
    const used = try reader.interface.readSliceShort(buffer);
    if (used == 0 or used == buffer.len) return error.InvalidVerifier;
    return buffer[0..used];
}

fn clearAuthentication() void {
    std.crypto.secureZero(u8, std.mem.asBytes(&catalog_verifiers));
    std.crypto.secureZero(u8, std.mem.asBytes(&catalog_credentials));
    std.crypto.secureZero(u8, std.mem.asBytes(&verifier));
    catalog_credentials = null;
    verifier = null;
}

fn loadCatalogCredentials(path: []const u8) !void {
    const prefix = "RAMSQL-SCRAM-V1\t";
    const file_limit = prefix.len + 64 + 2 + config.catalog_objects * (64 + 1 + 512 + 2);
    var region: owned_pages.Region = .{};
    try region.reserve(&memory_ledger, file_limit + 1);
    defer region.close() catch @panic("lost authentication file memory ownership");
    const buffer = region.memory.bytes[0 .. file_limit + 1];
    defer std.crypto.secureZero(u8, buffer);
    const source = try readCredentialFile(path, buffer);
    const line_end = std.mem.indexOfScalar(u8, source, '\n') orelse return error.InvalidVerifier;
    const header_end = if (line_end != 0 and source[line_end - 1] == '\r') line_end - 1 else line_end;
    const header = source[0..header_end];
    if (header.len != prefix.len + 64 or !std.mem.startsWith(u8, header, prefix)) return error.InvalidVerifier;
    var mock_key: [32]u8 = undefined;
    defer std.crypto.secureZero(u8, &mock_key);
    _ = std.fmt.hexToBytes(&mock_key, header[prefix.len..]) catch return error.InvalidVerifier;
    const entries = try scram.parseVerifierList(source[line_end + 1 ..], &catalog_verifiers);
    catalog_credentials = try scram.Credentials.init(entries, mock_key);
}

fn loadAuthentication(options: Options) !void {
    if (options.insecure_local) {
        if (options.auth_verifier_file != null) return error.InvalidServerOptions;
        return;
    }
    if (options.auth_credentials_file == null and options.auth_verifier_file == null) return error.AuthenticationRequired;
    if (options.protocol != .postgres or options.mysql_port != null or options.mssql_port != null) return error.AuthenticationUnsupportedForProtocol;
    if (options.auth_credentials_file) |path| return loadCatalogCredentials(path);
    var source_buffer: [513]u8 = undefined;
    defer std.crypto.secureZero(u8, &source_buffer);
    const source = try readCredentialFile(options.auth_verifier_file.?, &source_buffer);
    verifier = try scram.Verifier.parse(options.auth_user orelse "postgres", std.mem.trimEnd(u8, source, "\r\n"));
}

pub fn run(requested: Options) !void {
    var options = requested;
    if (options.auth_credentials_file != null and (options.auth_user != null or options.auth_verifier_file != null)) return error.InvalidServerOptions;
    bind_catalog_role = options.auth_catalog_role or options.auth_credentials_file != null;
    if (bind_catalog_role and (options.insecure_local or options.memory or options.create_data or
        (options.auth_verifier_file == null and options.auth_credentials_file == null))) return error.InvalidServerOptions;
    try validateClientLimit(options);
    if (options.idle_ms == 0) return error.InvalidServerOptions;
    if (options.workers != 0) try validateWorkers(options);
    const plan = try storagePlan(options);
    const observed = try initializeMemory(options);
    options.workers = try chooseWorkers(options, &plan, observed);
    try validateWorkers(options);
    if (options.max_clients > standard_capacity) {
        const worker_bytes = if (options.workers > 1) options.workers * worker_budget_each else 0;
        const startup = try storageCharges(options, &plan, worker_bytes);
        if (try expandedClientCharge(options, &plan, startup) > observed.new_region_bytes) return error.BudgetExceeded;
    }
    parallel = options.workers > 1;
    cluster_options = options;
    defer clearAuthentication();
    selected_work_rows=options.work_rows;selected_read_refs=plan.refs;
    var worker_grant:?resource_budget.Token=null;
    if(parallel) worker_grant=try memory_ledger.acquire(options.workers*worker_budget_each);
    defer if(worker_grant) |grant| memory_ledger.release(grant) catch @panic("lost worker stack grant");
    var client_memory:owned_pages.Region=.{};
    try client_memory.reserve(&memory_ledger,options.max_clients * @sizeOf(Client));
    defer client_memory.close() catch @panic("lost client metadata ownership");
    clients = try client_memory.memory.view(Client, 0, options.max_clients);
    for (clients) |*client| {
        client.socket = tcp.invalid_socket;
        client.state = .init(0); client.cancel_key = .init(0); client.cancel_request = .init(0);
    }
    admission_timeout_ns = @as(i64,@min(options.idle_ms,1000)) * 1000000;
    var backing:owned_pages.Region=.{};
    try openStorage(options,&backing);
    defer backing.close() catch @panic("lost database storage ownership");
    var workspace:owned_pages.Region=.{};
    try workspace.reserve(&memory_ledger,plan.workspace_bytes);
    defer workspace.close() catch @panic("lost query workspace ownership");
    try store.database.bindWorkspaceMemory(workspace.memory.bytes, options.work_rows);
    _ = try Raw.Result.rowBytes(options.result_rows);
    _ = try responseBytes(options.result_rows);
    selected_result_rows = options.result_rows;
    interrupt_max_clients = options.max_clients;
    zones.directory = options.timezone_dir;
    zones.in_use = &zoneInUse;
    configureStore(&store, options);
    try loadAuthentication(options);
    var tls_memory: owned_pages.Region = .{};
    defer {
        tls_slots = &.{};
        tls_credentials.close();
        tls_memory.close() catch @panic("lost TLS transport ownership");
    }
    if (options.tls_certificate_file) |certificate| {
        try tls_credentials.load(certificate, options.tls_key_file.?);
        try tls_memory.reserve(&memory_ledger, options.max_clients * @sizeOf(postgres_tls.Slot));
        tls_slots = try tls_memory.memory.view(postgres_tls.Slot, 0, options.max_clients);
    }

    var cache_region: owned_pages.Region = .{};
    defer cache_region.close() catch @panic("lost result cache memory ownership");
    if (plan.read_cache.entries != 0) try cache_region.reserve(&memory_ledger, plan.read_cache.bytes);
    defer persistence.close();
    defer if (options.database_cluster) {
        for (&cluster_slots) |*slot| slot.close();
        cluster_catalog.close();
    };
    if (options.database_cluster) try openCluster() else if (!options.memory) try openData(options);
    if (catalog_credentials) |*credentials| {
        for (credentials.verifiers) |*entry| {
            if (store.database.authenticatedRole(entry.identity()) == null) return error.AuthenticationFailed;
        }
    } else if (bind_catalog_role and store.database.authenticatedRole(verifier.?.identity()) == null) return error.AuthenticationFailed;
    var read_cache: Raw.ReadResultCache = undefined;
    if (plan.read_cache.entries != 0) {
        const cache_bytes = cache_region.memory.bytes;
        try read_cache.bindMemory(&store.database, cache_bytes[plan.read_cache.small_offset..][0..plan.read_cache.small_bytes],
            plan.read_cache.entries, read_cache_key_bytes, plan.read_cache.rows);
        if (plan.read_cache.projected_entries != 0) {
            try read_cache.bindProjectedMemory(cache_bytes[0..plan.read_cache.projected_bytes],
                plan.read_cache.projected_rows, projected_cache_min_scan_rows);
        }
        store.read_result_cache = &read_cache;
    }
    defer store.read_result_cache = null;
    try tcp.init();
    defer tcp.deinit();
    var listeners: [3]tcp.Socket = @splat(tcp.invalid_socket);
    var protocols: [3]Protocol = @splat(options.protocol);
    var count: usize = 1;
    const default_port: u16 = switch (options.protocol) {
        .postgres => 5432,
        .mysql => 3306,
        .mssql => 1433,
    };
    listeners[0] = try tcp.listen(.{ 127, 0, 0, 1 }, options.port orelse default_port);
    defer for (listeners[0..count]) |listener| tcp.close(listener);
    if (options.mysql_port) |port| {
        protocols[count] = .mysql;
        listeners[count] = try tcp.listen(.{ 127, 0, 0, 1 }, port);
        count += 1;
    }
    if (options.mssql_port) |port| {
        protocols[count] = .mssql;
        listeners[count] = try tcp.listen(.{ 127, 0, 0, 1 }, port);
        count += 1;
    }
    interrupt_listener = if (options.protocol == .postgres) listeners[0] else tcp.invalid_socket;
    defer interrupt_listener = tcp.invalid_socket;
    defer for (&overflow_cancels) |*pending| pending.close();
    for (listeners[0..count], protocols[0..count]) |listener, protocol| {
        var buffer: [128]u8 = undefined;
        const line = try sig.fmt.bufPrint(&buffer, "RAMSQL {s} 127.0.0.1:{d}\n", .{ @tagName(protocol), try tcp.localPort(listener) });
        try sig.io.File.stdout().writeStreamingAll(.{}, line);
    }
    defer for (clients) |*client| if (client.socket != tcp.invalid_socket) {
        client.close();
    };
    if (parallel) return runParallel(options, listeners[0..count], protocols[0..count]);
    // Probe once at startup, then accept only after readiness. Queues that
    // exceed the bounded accept batch stay readable on the next poll. The
    // separate execution hook still accepts CancelRequests during long SQL.
    var listener_ready: [3]bool = @splat(true);
    while (true) {
        // Bound accept work so connection churn cannot monopolize the reactor.
        for (listeners[0..count], protocols[0..count], listener_ready[0..count]) |listener, protocol, ready| {
            if (ready) try acceptConnections(listener, protocol, options.max_clients, capacity, null);
        }
        pollOverflowCancels(sig.os.clockMonotonicNs());
        var sockets: [capacity + 2]tcp.Socket = @splat(tcp.invalid_socket);
        var readable: [capacity + 2]bool = @splat(false);
        var writable: [capacity + 2]bool = @splat(false);
        const now = sig.os.clockMonotonicNs();
        store.now_ns = now;
        var buffered = false;
        // Wake parked clients whose lock may have been released, whose timer
        // is due or that were canceled, in wait-start (FIFO) order.
        var woken: [capacity]usize = undefined;
        var woken_count: usize = 0;
        for (clients[0..options.max_clients], 0..) |*client, index| {
            if (client.socket == tcp.invalid_socket or !client.blocked) continue;
            if (options.database_cluster) client.connection.store.now_ns = now;
            if (!client.connection.wakeReady()) continue;
            var position = woken_count;
            while (position != 0 and clients[woken[position - 1]].connection.waitStart() > client.connection.waitStart()) : (position -= 1) woken[position] = woken[position - 1];
            woken[position] = index;
            woken_count += 1;
        }
        for (woken[0..woken_count]) |index| {
            const client = &clients[index];
            if (client.socket == tcp.invalid_socket) continue;
            client.blocked = false;
            if (client.serve()) buffered = true;
        }
        for (clients[0..options.max_clients], 0..) |*client, index| {
            if (client.socket == tcp.invalid_socket) continue;
            client.expireSessionTimeout(now);
            if (client.socket == tcp.invalid_socket) continue;
            // The idle timeout does not apply while a statement waits.
            if (client.blocked) client.touched = now;
            if (now - client.touched >= @as(i64, options.idle_ms) * 1000000) {
                client.close();
                continue;
            }
            if (client.serve()) buffered = true;
            if (client.socket == tcp.invalid_socket) continue;
            sockets[index] = client.socket;
            readable[index] = client.wantsRead();
            writable[index] = client.wantsWrite();
        }
        const listener_offset = if (comptime capacity <= standard_capacity) capacity else options.max_clients;
        for (1..count) |index| {
            sockets[listener_offset + index - 1] = listeners[index];
            readable[listener_offset + index - 1] = true;
        }
        // Poll timeout: the nearest session or parked statement deadline.
        var timeout: u32 = @min(options.idle_ms, 1000);
        const poll_now = monotonicClock();
        for (&overflow_cancels) |*pending| if (pending.socket != tcp.invalid_socket) { timeout = @min(timeout,10); break; };
        for (clients[0..options.max_clients]) |*client| {
            if (client.socket == tcp.invalid_socket) continue;
            if (client.sessionDeadline()) |due| timeout = deadlinePollMs(poll_now, due, timeout);
            if (!client.blocked) continue;
            if (client.connection.wakeReady()) {
                timeout = 0;
                continue;
            }
            const due = client.connection.nextDeadline() orelse continue;
            timeout = deadlinePollMs(poll_now, due, timeout);
        }
        const poll_count = if (comptime capacity <= standard_capacity) sockets.len else listener_offset + count - 1;
        listener_ready[0] = try waitServer(listeners[0], sockets[0..poll_count], readable[0..poll_count], writable[0..poll_count], if (buffered) 0 else timeout);
        for (1..count) |index| listener_ready[index] = readable[listener_offset + index - 1];
        for (clients[0..options.max_clients], 0..) |*client, index| {
            if (client.socket == tcp.invalid_socket) continue;
            if (writable[index]) {
                client.flush() catch {
                    client.close();
                    continue;
                };
            }
            if (client.socket == tcp.invalid_socket) continue;
            if (readable[index]) client.receive(false) catch client.close();
        }
    }
}

/// Offline backup validates/replays the existing source before publishing a copy.
pub fn bootstrapCheckpoint(options: Options) !void {
    if (options.checkpoint_stem == null or options.checkpoint_id == null or options.checkpoint_every != 0 or
        options.tls_certificate_file != null or options.tls_key_file != null) return error.InvalidServerOptions;
    const plan = try storagePlan(options);
    _ = try initializeMemory(options);
    var backing: owned_pages.Region = .{};
    try openStorage(options, &backing);
    defer backing.close() catch @panic("lost checkpoint storage ownership");
    var workspace: owned_pages.Region = .{};
    try workspace.reserve(&memory_ledger, plan.workspace_bytes);
    defer workspace.close() catch @panic("lost checkpoint workspace ownership");
    try store.database.bindWorkspaceMemory(workspace.memory.bytes, options.work_rows);
    _ = try persistence.initExisting(options.data, &store, &recovery_buffer);
    defer persistence.close();
    try persistence.bootstrapCheckpoint(options.checkpoint_stem.?, checkpointIdentity(options), &recovery_buffer);
}

pub fn backup(options: Options, target: []const u8) !void {
    if (options.checkpoint_stem != null) return error.InvalidServerOptions;
    if (options.memory or options.database_cluster or options.tls_certificate_file != null or options.tls_key_file != null) return error.InvalidServerOptions;
    _ = try initializeMemory(options);
    var backing:owned_pages.Region=.{};
    try openStorage(options,&backing);
    defer backing.close() catch @panic("lost backup storage ownership");
    var workspace:owned_pages.Region=.{};
    try workspace.reserve(&memory_ledger,(try storagePlan(options)).workspace_bytes);
    defer workspace.close() catch @panic("lost backup workspace ownership");
    try store.database.bindWorkspaceMemory(workspace.memory.bytes, options.work_rows);
    store.prepared_limit = options.max_prepared_transactions;
    _ = try persistence.initExisting(options.data, &store, &recovery_buffer);
    defer persistence.close();
    try @import("backup.sig").create(&persistence.file, target, &recovery_buffer);
}
