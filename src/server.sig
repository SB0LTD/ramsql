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

pub const Protocol = enum { postgres, mysql, mssql };
pub const table_capacity = profile.tables;
pub const Options = struct {
    protocol: Protocol = .postgres,
    port: ?u16 = null,
    mysql_port: ?u16 = null,
    mssql_port: ?u16 = null,
    max_clients: usize = profile.clients,
    /// Reactors and concurrent simple readers; mutations remain exclusive.
    workers: usize = 1,
    memory: bool = false,
    data: []const u8 = "ramsql.wal",
    insecure_local: bool = false,
    auth_user: []const u8 = "postgres",
    auth_verifier_file: ?[]const u8 = null,
    idle_ms: u32 = 30000,
    /// Execution checkpoints per statement; zero disables the work limit.
    max_query_work: u64 = 0,
    /// Runtime storage budget, independent of result/query workspace sizes.
    storage_rows: usize = profile.rows,
    storage_versions: ?usize = null,
    table_rows: ?[profile.tables]usize = null,
    table_versions: ?[profile.tables]usize = null,
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
const config: sql.Config = .{
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
};
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
const PG = pg.Session(DB);
const MySQL = mysql.Session(DB);
const TDS = tds.Session(DB);
const capacity = 60;
const input_capacity = 65536;
const output_capacity = @max(PG.responseCapacity(), @max(MySQL.responseCapacity(), TDS.responseCapacity())) + 32768;
const Session = union(Protocol) { postgres: PG, mysql: MySQL, mssql: TDS };
const Reply = struct { consumed: usize, written: usize, close: bool, blocked: bool, cancel: ?pg.Cancel = null };
const Client = struct {
    // Only the acceptor initializes state 1. Only the assigned reactor owns
    // state 2. Publish 0 only after disconnect and all page releases finish.
    state: std.atomic.Value(u32) = .init(0),
    cancel_key: std.atomic.Value(u64) = .init(0),
    cancel_request: std.atomic.Value(u64) = .init(0),
    socket: tcp.Socket = tcp.invalid_socket,
    connection: DB = .{ .store = &store, .id = 0 },
    session: Session = .{ .postgres = .{} },
    input: [input_capacity]u8 = undefined,
    input_len: usize = 0,
    input_start: usize = 0,
    output: []u8 = &.{},
    result_region: owned_pages.Region = .{},
    output_region: owned_pages.Region = .{},
    read_metadata:owned_pages.Region=.{},
    read_scratch:owned_pages.Region=.{},
    read_context:?*Raw.ReadContext=null,
    read_deadline:?i64=null,
    read_retry_at:i64=0,
    output_len: usize = 0,
    output_sent: usize = 0,
    closing: bool = false,
    touched: i64 = 0,
    /// A statement of this client is parked on a lock.
    blocked: bool = false,
    cancel_secret: u32 = 0,

    fn close(self: *Client) void {
        const active = self.state.load(.acquire) == 2;
        if (active) lockStore();
        self.cancel_key.store(0, .release);
        if (active) self.connection.disconnect();
        self.state.store(1, .release);
        if (active) unlockStore();
        if(self.socket!=tcp.invalid_socket) tcp.close(self.socket);
        lockMemory();
        self.result_region.close() catch @panic("lost client result ownership");
        self.output_region.close() catch @panic("lost client response ownership");
        self.read_context=null;
        self.read_scratch.close() catch @panic("lost read workspace ownership");
        self.read_metadata.close() catch @panic("lost read context ownership");
        unlockMemory();
        self.output = &.{};
        self.socket = tcp.invalid_socket;
        self.blocked = false;
        // The owner publishes a closed slot as free on its next iteration,
        // after callers of close have stopped inspecting the old connection.
        self.state.store(if (parallel and active) 3 else 0, .release);
    }

    fn dispatch(self: *Client) !void {
        if (self.blocked or self.output_sent != 0 or self.closing or (self.input_len == 0 and !self.connection.copyBlocked())) return;
        // Cancellation is control traffic and must not queue behind SQL held
        // by another reactor. An established session cannot send startup frames.
        if (parallel and self.session == .postgres and !self.session.postgres.startup) {
            if (pg.parseCancel(self.input[self.input_start..][0..self.input_len])) |key| {
                cancelBackend(key); self.consume(16); self.closing = true; return;
            }
        }
        if(try self.dispatchRead()) return;
        lockStore();
        defer unlockStore();
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
        if (self.blocked or self.output_sent != 0 or self.closing or (self.input_len == 0 and !parked_copy) or self.output.len - self.output_len < required) return;
        const input = self.input[self.input_start..][0..self.input_len];
        const output = self.output[self.output_len..];
        store.now_ns = sig.os.clockMonotonicNs();
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
        if (reply.consumed > self.input_len or reply.written > output.len) return error.InvalidReply;
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
        } else if (self.input_len == self.input.len) return error.RequestTooLarge;
        self.output_len += reply.written;
        self.closing = reply.close;
    }

    fn provisionRead(self:*Client) !void {
        if(self.read_context!=null) return;
        lockMemory();defer unlockMemory();
        try refreshMemoryBudget();
        try self.read_metadata.reserve(&memory_ledger,@sizeOf(Raw.ReadContext));
        errdefer self.read_metadata.close() catch @panic("lost failed reader metadata");
        try self.read_scratch.reserve(&memory_ledger,try Raw.ReadContext.backingBytesFor(selected_work_rows,selected_read_refs));
        errdefer self.read_scratch.close() catch @panic("lost failed reader scratch");
        const contexts=try self.read_metadata.memory.view(Raw.ReadContext,0,1);
        contexts[0]=.{};
        try contexts[0].bindMemoryFor(self.read_scratch.memory.bytes,selected_work_rows,selected_read_refs);
        self.read_context=&contexts[0];
    }
    fn pollRead(raw:?*anyopaque) Raw.SqlError!void {
        const self:*Client=@ptrCast(@alignCast(raw.?));
        const requested=self.cancel_request.swap(0,.acq_rel);
        if(requested!=0 and requested==self.cancel_key.load(.acquire)) return error.QueryCanceled;
        if(self.read_deadline) |deadline| if(monotonicClock()>=deadline) return error.QueryCanceled;
    }
    fn dispatchRead(self:*Client) !bool {
        if(!parallel or self.session!=.postgres or !self.connection.detachedReadEligible()) return false;
        const session=&self.session.postgres;
        if(!session.startup or session.failed or session.copy.mode!=.none or session.active_portal!=null or
            (session.authentication!=null and session.authentication.?.state!=.complete)) return false;
        const input=self.input[self.input_start..][0..self.input_len];
        if(input.len<5 or input[0]!='Q') return false;
        const size=std.mem.readInt(u32,input[1..5],.big);
        if(size<5 or size>pg.max_packet or size+1>input.len or input[size]!=0) return false;
        const source=input[5..size];
        if(std.mem.indexOfScalar(u8,source,0)!=null) return false;
        const first=std.mem.trimStart(u8,source," \t\r\n");
        if(!std.ascii.startsWithIgnoreCase(first,"select") and !std.ascii.startsWithIgnoreCase(first,"with")) return false;
        const required=try session.requiredCapacity();
        if(self.output.len-self.output_len<required) return false;
        if(self.read_context==null and monotonicClock()<self.read_retry_at) return false;
        self.provisionRead() catch {self.read_retry_at=monotonicClock()+1000000000;return false;};
        data_gate.lockRead();defer data_gate.unlockRead();
        const context=self.read_context.?;
        self.cancel_request.store(0,.release);
        self.connection.execution.reset(store.max_query_work);
        self.connection.execution.context=self;self.connection.execution.poll=pollRead;
        self.read_deadline=if(self.connection.settings.statement_timeout_ms==0) null else monotonicClock()+@as(i64,self.connection.settings.statement_timeout_ms)*1000000;
        const statement_us=wallClock();
        context.session=self.connection.detachedReadSession(&self.connection.execution,statement_us);
        context.dialect=.postgres;
        var prepared=true;
        context.prepare(&store.database,source,.{.csn=store.database.csn}) catch |err| switch(err) {
            error.QueryCanceled,error.QueryWorkLimit=>{prepared=false;},
            else=>return false,
        };
        // Dynamic named-zone resolution retains the existing serialized cache
        // ownership. Typed temporal values use the immutable session zone.
        if(prepared and context.needsZoneResolver()) return false;
        self.connection.read_context=context;self.connection.running_statement=true;
        defer {self.connection.read_context=null;self.connection.running_statement=false;self.read_deadline=null;}
        const output=self.output[self.output_len..];
        const reply=try session.handle(&self.connection,input,output);
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

    fn flush(self: *Client) !void {
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
        if (self.closing and self.output_len == 0) self.close();
    }

    /// Dispatches at most 32 buffered packets, then flushes. Returns true
    /// when packets remain buffered (the reactor should poll without waiting).
    fn serve(self: *Client) bool {
        var frames: usize = 0;
        while (frames < 32) : (frames += 1) {
            const before = self.input_len;
            self.dispatch() catch {
                self.close();
                return false;
            };
            if (self.blocked or before == self.input_len or self.closing) break;
        }
        self.flush() catch {
            self.close();
            return false;
        };
        if (self.socket == tcp.invalid_socket) return false;
        return frames == 32 and self.input_len != 0 and self.output_len == 0;
    }
};
var store: Store = .{};
var persistence: Persistence = .{};
// profile.transaction_bytes sizes this recovery buffer, which Persistence also
// hands to the store as the change-record encode buffer: any record written is
// recoverable.
var recovery_buffer: [profile.transaction_bytes]u8 = undefined;
var verifier: ?scram.Verifier = null;
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
var memory_slots:[capacity*4+4]resource_budget.Slot=undefined;
var memory_hard:?u64=null;
var memory_headroom:?u64=null;
var memory_refresh_at:i64=0;
const worker_budget_each:u64=5*1024*1024;
fn lockMemory() void {if(parallel) memory_mutex.lock();}
fn unlockMemory() void {if(parallel) memory_mutex.unlock();}
fn initializeMemory(options:Options) !void {
    memory_hard=options.memory_budget;memory_headroom=options.os_headroom;
    const observed=try machine_capacity.sample(memory_hard,memory_headroom,0);
    try memory_ledger.bind(&memory_slots,observed.new_region_bytes);
    memory_refresh_at=monotonicClock()+1000000000;
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
    const a=@addWithOverflow(result,page-1);const b=@addWithOverflow(output,page-1);
    if(a[1]!=0 or b[1]!=0) return false;
    const required=@addWithOverflow(a[0]/page*page,b[0]/page*page);
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
    if (target.socket == tcp.invalid_socket or target.session != .postgres) return;
    if (!sameSecret(target.cancel_secret, key.secret)) return;
    target.connection.requestCancel();
}

fn monotonicClock() i64 {
    return sig.os.clockMonotonicNs();
}

var interrupt_listener: tcp.Socket = tcp.invalid_socket;
var interrupt_max_clients: usize = 0;
var next_interrupt_poll: i64 = 0;
var admission_timeout_ns: i64 = 1000000000;

// A bounded control/admission queue preserves cancellation when the SQL pool is
// full, negotiates SSL/GSS refusal, and returns a real 53300 startup error.
const OverflowCancel = struct {
    socket: tcp.Socket = tcp.invalid_socket,
    bytes: [pg.max_packet]u8 = undefined,
    used: usize = 0,
    expected: usize = 0,
    output: [256]u8 = undefined,
    output_len: usize = 0,
    output_sent: usize = 0,
    closing: bool = false,
    deadline: i64 = 0,

    fn close(self: *OverflowCancel) void {
        if (self.socket != tcp.invalid_socket) tcp.close(self.socket);
        self.socket = tcp.invalid_socket;
    }

    fn handOff(self: *OverflowCancel) bool {
        if (!clientMemoryAvailable()) return false;
        for (clients,0..) |*client,slot| if (client.state.load(.acquire) == 0) {
            const socket = self.socket;
            configureClient(socket,.postgres,slot,self.bytes[0..self.used]) catch |err| {
                switch(err) {
                    error.BudgetExceeded,error.TokenCapacity,error.MemoryReservationFailed=>{
                        self.output_len=pg.memoryLimitError(&self.output) catch {self.close();return true;};
                        self.output_sent=0;self.closing=true;
                    },
                    else=>self.close(),
                }
                return true;
            };
            self.socket = tcp.invalid_socket;
            return true;
        };
        return false;
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
        for (0..4) |_| {
            const target = if (self.used < 4) @as(usize,4) else if (self.used < 8) @as(usize,8) else self.expected;
            if (self.used < target) {
                const got = tcp.recv(self.socket, self.bytes[self.used..target]) catch { self.close(); return; } orelse return;
                if (got == 0) { self.close(); return; }
                self.used += got;
            }
            if (self.used < 4) continue;
            if (self.expected == 0) {
                self.expected = std.mem.readInt(u32,self.bytes[0..4],.big);
                if (self.expected < 8 or self.expected > self.bytes.len) {
                    // Let the normal session emit its fatal protocol error.
                    if (!self.handOff()) self.close();
                    return;
                }
            }
            if (self.used < 8) continue;
            const code = std.mem.readInt(u32,self.bytes[4..8],.big);
            if (self.expected == 8 and (code == 80877103 or code == 80877104)) {
                self.output[0]='N'; self.output_len=1; self.used=0; self.expected=0;
                return;
            }
            if (self.used < self.expected) continue;
            if (pg.parseCancel(self.bytes[0..self.used])) |key| {
                cancelBackend(key); self.close(); return;
            }
            // Transfer complete startup, including malformed fields, to the
            // normal protocol validator. Cancel requires no SQL worker slot.
            if (self.handOff()) return;
            if (code != 196608) { self.close(); return; }
            self.output_len = (if(!clientMemoryAvailable()) pg.memoryLimitError(&self.output) else pg.connectionLimitError(&self.output)) catch { self.close(); return; };
            self.closing = true;
            return;
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
        acceptConnections(interrupt_listener, .postgres, interrupt_max_clients, 4) catch {};
    pollOverflowCancels(now);
    for (clients[0..interrupt_max_clients]) |*client| {
        if (client.socket == tcp.invalid_socket or client.session != .postgres or client.session.postgres.startup or client.closing or client.input_start != 0) continue;
        if (client.input_len < 16) {
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
fn acceptConnections(listener: tcp.Socket, protocol: Protocol, max_clients: usize, limit: usize) !void {
    for (0..limit) |_| {
        const socket = tcp.accept(listener) catch break orelse break;
        if (protocol == .postgres) {
            if (!queueOverflowCancel(socket)) tcp.close(socket);
            continue;
        }
        var free: ?usize = null;
        for (clients[0..max_clients], 0..) |*candidate, index| if (candidate.state.load(.acquire) == 0) {
            free = index;
            break;
        };
        const slot = free orelse {
            if (protocol != .postgres or !queueOverflowCancel(socket)) tcp.close(socket);
            continue;
        };
        configureClient(socket,protocol,slot,&.{}) catch {tcp.close(socket);continue;};
    }
}

fn configureClient(socket: tcp.Socket, protocol: Protocol, slot: usize, initial: []const u8) !void {
    const client = &clients[slot];
    client.state.store(1, .release);
    client.cancel_key.store(0, .release);
    client.cancel_request.store(0, .release);
    client.socket = socket;
    client.result_region = .{};
    client.output_region = .{};
    client.read_metadata=.{};client.read_scratch=.{};client.read_context=null;client.read_deadline=null;client.read_retry_at=0;
    client.output = &.{};
    client.blocked = false;
    client.connection = .{ .store = &store, .id = next_id };
    next_id +%= 1;
    if (next_id == 0) next_id = 1;
    client.session = switch (protocol) {
        .postgres => .{ .postgres = .{} },
        .mysql => .{ .mysql = .{} },
        .mssql => .{ .mssql = .{} },
    };
    // The accept/admission queue retains the socket on setup failure so it can
    // emit a PostgreSQL resource error instead of silently resetting TCP.
    errdefer {client.socket=tcp.invalid_socket;client.close();}
    try provisionClient(client);
    if (protocol == .postgres) {
        // BackendKeyData: pid = slot + 1, fresh 32-bit secret.
        var secret: [4]u8 = undefined;
        try entropy.fill(&secret);
        client.cancel_secret = std.mem.readInt(u32, &secret, .little);
        client.session.postgres.configureBackendKey(@intCast(slot + 1), client.cancel_secret);
        if (verifier) |*credential| {
            var random: [24]u8 = undefined;
            try entropy.fill(&random);
            var nonce: [32]u8 = undefined;
            _ = std.base64.standard.Encoder.encode(&nonce, &random);
            try client.session.postgres.configureAuthentication(credential, &nonce);
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
    if (protocol == .postgres) client.cancel_key.store((@as(u64, client.cancel_secret) << 1) | 1, .release);
    client.state.store(2, .release);
    if (parallel) reactors[slot % reactor_count].signal.notify();
}

const max_reactors = 16;
var reactor_count: usize = 0;
var reactor_stop: std.atomic.Value(bool) = .init(false);
var reactor_failed: std.atomic.Value(bool) = .init(false);
var reactors: [max_reactors]Reactor = @splat(.{});

fn validateWorkers(options: Options) !void {
    if (options.workers == 0 or options.workers > max_reactors or options.workers > options.max_clients) return error.InvalidServerOptions;
    if (options.workers > 1 and !threads.available) return error.WorkersUnsupportedOnPlatform;
}

const Reactor = struct {
    thread: threads.Thread = .{},
    signal: threads.Signal = .{},
    index: usize = 0,
    idle_ms: u32 = 30000,

    fn entry(context: ?*anyopaque) void {
        const self: *Reactor = @ptrCast(@alignCast(context.?));
        self.run() catch {
            reactor_failed.store(true, .release);
            reactor_stop.store(true, .release);
            for (&reactors) |*reactor| reactor.signal.notify();
        };
    }

    fn run(self: *Reactor) !void {
        while (!reactor_stop.load(.acquire)) {
            const epoch = self.signal.epoch.load(.acquire);
            var sockets: [capacity]tcp.Socket = @splat(tcp.invalid_socket);
            var readable: [capacity]bool = @splat(false);
            var writable: [capacity]bool = @splat(false);
            var active: usize = 0;
            var buffered = false;
            const now = sig.os.clockMonotonicNs();
            // Shared store state, timers and cancellation are read only while
            // holding the same mutex used for execution and disconnection.
            var woken: [capacity]usize = undefined;
            var woken_count: usize = 0;
            var slot = self.index;
            while (slot < clients.len) : (slot += reactor_count) {
                const client = &clients[slot];
                if (client.state.load(.acquire) == 3) client.state.store(0, .release);
            }
            if (store_mutex.tryLock()) {
            store.now_ns = now;
            slot = self.index;
            while (slot < clients.len) : (slot += reactor_count) {
                const client = &clients[slot];
                if (client.state.load(.acquire) != 2) continue;
                pollWorkerInterrupts(client, client.connection.id);
                if (!client.blocked or !client.connection.wakeReady()) continue;
                var position = woken_count;
                while (position != 0 and clients[woken[position-1]].connection.waitStart() > client.connection.waitStart()) : (position -= 1) woken[position] = woken[position-1];
                woken[position] = slot; woken_count += 1;
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
                if (client.blocked) client.touched = now;
                if (now-client.touched >= @as(i64,self.idle_ms)*1000000) { client.close(); continue; }
                if (client.serve()) buffered = true;
                if (client.state.load(.acquire) != 2) continue;
                sockets[slot] = client.socket;
                readable[slot] = client.output_len == 0 and client.input_len < client.input.len;
                writable[slot] = client.output_len != 0;
                active += 1;
            }
            if (active == 0) { self.signal.wait(epoch, 100); continue; }
            // Finite poll bounds new admission, cancellation and shutdown
            // latency without introducing a second socket wakeup protocol.
            _ = try tcp.waitMasked(tcp.invalid_socket, &sockets, &readable, &writable, if (buffered) 0 else 10);
            slot = self.index;
            while (slot < clients.len) : (slot += reactor_count) {
                const client = &clients[slot];
                if (client.state.load(.acquire) != 2) continue;
                if (writable[slot]) client.flush() catch { client.close(); continue; };
                if (client.state.load(.acquire) != 2) continue;
                if (readable[slot] and client.output_len == 0) {
                    if (client.input_start != 0) {
                        for (0..client.input_len) |offset| client.input[offset] = client.input[client.input_start+offset];
                        client.input_start = 0;
                    }
                    if (tcp.recv(client.socket,client.input[client.input_len..]) catch { client.close(); continue; }) |received| {
                        if (received == 0) { client.close(); continue; }
                        client.input_len += received;
                        client.touched = sig.os.clockMonotonicNs();
                    }
                }
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
    for (reactors[0..reactor_count],0..) |*reactor,index| {
        reactor.* = .{ .index=index, .idle_ms=options.idle_ms };
        try reactor.thread.start(&Reactor.entry, reactor);
        started += 1;
    }
    while (!reactor_stop.load(.acquire)) {
        for (listeners,protocols) |listener,protocol| try acceptConnections(listener,protocol,options.max_clients,capacity);
        pollOverflowCancels(sig.os.clockMonotonicNs());
        var sockets: [2]tcp.Socket = @splat(tcp.invalid_socket);
        var readable: [2]bool = @splat(false);
        var writable: [2]bool = @splat(false);
        for (1..listeners.len) |index| { sockets[index-1]=listeners[index]; readable[index-1]=true; }
        _ = try tcp.waitMasked(listeners[0],&sockets,&readable,&writable,10);
    }
    if (reactor_failed.load(.acquire)) return error.ReactorFailed;
}

fn storagePlan(options: Options) !struct { max_rows: usize, versions: usize, budgets: [profile.tables]Raw.TableBudget, refs: usize, bytes: usize, workspace_bytes: usize, result_bytes: usize, response_bytes: usize } {
    if (options.storage_rows == 0 or options.storage_rows > 0x3fffffff) return error.InvalidServerOptions;
    var budgets: [profile.tables]Raw.TableBudget = undefined;
    var max_rows: usize = 0; var max_versions: usize = 0;
    for (&budgets,0..) |*budget,index| {
        const rows = if (options.table_rows) |values| values[index] else options.storage_rows;
        if (rows == 0 or rows > 0x3fffffff) return error.InvalidServerOptions;
        const versions = if (options.table_versions) |values| values[index] else options.storage_versions orelse if (rows == profile.rows) profile.versions else rows * 2;
        if (versions <= rows or versions > 0x7fffffff) return error.InvalidServerOptions;
        budget.* = .{ .rows=rows, .versions=versions };
        max_rows=@max(max_rows,rows); max_versions=@max(max_versions,versions);
    }
    const ref_count = @max(max_rows * 4, Raw.queryReferenceCount(options.work_rows) catch return error.InvalidServerOptions);
    return .{ .max_rows=max_rows, .versions = max_versions, .budgets=budgets, .refs = ref_count, .bytes = try Raw.backingBytesFor(budgets, ref_count),
        .workspace_bytes = Raw.queryWorkspaceBytes(options.work_rows) catch return error.InvalidServerOptions,
        .result_bytes = Raw.Result.rowBytes(options.result_rows) catch return error.InvalidServerOptions,
        .response_bytes = responseBytes(options.result_rows) catch return error.InvalidServerOptions };
}

pub fn describe(options: Options) !void {
    if (options.max_clients == 0 or options.max_clients > capacity or options.idle_ms == 0) return error.InvalidServerOptions;
    try validateWorkers(options);
    const plan = try storagePlan(options);
    var buffer: [4096]u8 = undefined;
    const message = try sig.fmt.bufPrint(&buffer,
        "{{\"schema\":1,\"version\":\"{s}\",\"release_stage\":\"beta\",\"production_ready\":false,\"default_protocol\":\"postgres\",\"bind\":\"127.0.0.1\",\"tls\":false,\"roles\":false,\"isolation\":\"read_committed\",\"workers\":{d},\"sql_execution_workers\":{d},\"exclusive_sql_workers\":1,\"parallel_simple_reads\":{s},\"native_workers_available\":{s},\"libc_linked\":{s},\"cpu_model\":\"{s}\",\"build_mode\":\"{s}\",\"rows_per_table\":{d},\"versions_per_table\":{d},\"tables\":{d},\"columns\":{d},\"result_rows\":{d},\"work_rows\":{d},\"text_bytes\":{d},\"name_bytes\":{d},\"clients\":{d},\"compiled_clients\":{d},\"transaction_bytes\":{d},\"tx_writes\":{d},\"row_locks\":{d},\"predicates\":{d},\"expr_nodes\":{d},\"expr_depth\":{d},\"relations\":{d},\"subqueries\":{d},\"ctes\":{d},\"savepoints\":{d},\"sequences\":{d},\"numeric_digits\":{d},\"json_depth\":{d},\"copy_line_bytes\":{d},\"pg_packet_bytes\":{d},\"pg_prepared_sql_bytes\":{d},\"pg_statements\":{d},\"pg_portals\":{d},\"pg_parameters\":{d},\"input_bytes\":{d},\"output_bytes_per_client\":{d},\"backing_bytes\":{d},\"query_workspace_bytes\":{d},\"result_bytes_per_client\":{d},\"client_metadata_bytes\":{d},\"summary_groups\":{d},\"compact_integral_scans\":{s},\"wal_checkpointing\":false,\"mysql_tds_authentication\":false}}\n",
        .{ @import("ramsql").version, options.workers, options.workers, if(options.workers>1) "true" else "false", if (threads.available) "true" else "false", if (@import("builtin").link_libc) "true" else "false", @import("builtin").cpu.model.name, @tagName(@import("builtin").mode), plan.max_rows, plan.versions, config.tables, config.columns, options.result_rows, options.work_rows, config.text_bytes, config.name_bytes, options.max_clients, capacity, profile.transaction_bytes, config.tx_writes, config.row_locks, config.predicates, config.expr_nodes, config.expr_depth, config.relations, config.subqueries, config.ctes, config.savepoints, config.sequences, config.numeric_digits, config.json_depth, config.copy_line_bytes, pg.max_packet, pg.sql_capacity, pg.statement_capacity, pg.portal_capacity, pg.parameter_capacity, input_capacity, plan.response_bytes, plan.bytes, plan.workspace_bytes, plan.result_bytes, options.max_clients * @sizeOf(Client), config.summary_groups, if(config.compact_integral_scans and config.columns <= 64) "true" else "false" });
    const out = sig.io.File.stdout();
    try out.writeStreamingAll(.{}, message[0..message.len-2]);
    try out.writeStreamingAll(.{}, ",\"table_rows\":[");
    var number: [32]u8 = undefined;
    for (plan.budgets,0..) |budget,index| {
        if (index!=0) try out.writeStreamingAll(.{},",");
        try out.writeStreamingAll(.{},try sig.fmt.bufPrint(&number,"{d}",.{budget.rows}));
    }
    try out.writeStreamingAll(.{},"],\"table_versions\":[");
    for (plan.budgets,0..) |budget,index| {
        if (index!=0) try out.writeStreamingAll(.{},",");
        try out.writeStreamingAll(.{},try sig.fmt.bufPrint(&number,"{d}",.{budget.versions}));
    }
    var policy_buffer:[192]u8=undefined;
    try out.writeStreamingAll(.{},try std.fmt.bufPrint(&policy_buffer,
        "],\"server_region_budgeting\":true,\"memory_budget_bytes\":{?d},\"os_headroom_bytes\":{?d}}}\n",
        .{options.memory_budget,options.os_headroom}));
}

pub fn describeMachine(options:Options) !void {
    if(options.max_clients==0 or options.max_clients>capacity) return error.InvalidServerOptions;
    try validateWorkers(options);
    const plan=try storagePlan(options);
    const worker_bytes=if(options.workers>1) options.workers*worker_budget_each else 0;
    var total:u64=0;
    for([_]u64{plan.bytes,plan.workspace_bytes,options.max_clients*@sizeOf(Client),worker_bytes}) |amount| {
        const next=@addWithOverflow(total,amount);if(next[1]!=0) return error.ArithmeticOverflow;total=next[0];
    }
    try machine_capacity.describe(options.memory_budget,options.os_headroom,total,options.workers);
}
fn openStorage(options: Options,backing:*owned_pages.Region) !void {
    const plan = try storagePlan(options);
    try backing.reserve(&memory_ledger,plan.bytes);
    errdefer backing.close() catch @panic("lost failed storage ownership");
    try store.database.bindMemoryFor(backing.memory.bytes, plan.budgets, plan.refs);
}

pub fn run(options: Options) !void {
    if (options.max_clients == 0 or options.max_clients > capacity or options.idle_ms == 0) return error.InvalidServerOptions;
    try validateWorkers(options);
    parallel = options.workers > 1;
    try initializeMemory(options);
    const plan = try storagePlan(options);
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
    if (wall_clock.available) store.wall_clock = &wallClock;
    store.monotonic_clock = &monotonicClock;
    store.max_query_work = options.max_query_work;
    store.interrupt_poll = if (parallel) &pollWorkerInterrupts else &pollInterrupts;
    interrupt_max_clients = options.max_clients;
    zones.directory = options.timezone_dir;
    zones.in_use = &zoneInUse;
    store.zone_provider = .{ .context = @ptrCast(&zones), .lookup = &Zones.lookup };
    if (!options.insecure_local) {
        const verifier_path = options.auth_verifier_file orelse return error.AuthenticationRequired;
        if (options.protocol != .postgres or options.mysql_port != null or options.mssql_port != null) return error.AuthenticationUnsupportedForProtocol;
        const file = try sig.io.Dir.cwd().openFile(.{}, verifier_path, .{});
        defer file.close(.{});
        var source: [513]u8 = undefined;
        var reader = file.reader(.{}, &.{});
        var used: usize = 0;
        while (used < source.len) {
            const n = try reader.interface.readSliceShort(source[used..]);
            if (n == 0) break;
            used += n;
        }
        if (used == 0 or used > 512) return error.InvalidVerifier;
        while (used != 0 and (source[used - 1] == '\n' or source[used - 1] == '\r')) used -= 1;
        verifier = try scram.Verifier.parse(options.auth_user, source[0..used]);
        std.crypto.secureZero(u8, &source);
    } else if (options.auth_verifier_file != null) return error.InvalidServerOptions;
    if (!options.memory) {
        _ = try persistence.init(options.data, &store, &recovery_buffer);
    }
    defer persistence.close();
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
            if (ready) try acceptConnections(listener, protocol, options.max_clients, capacity);
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
            if (client.socket == tcp.invalid_socket or !client.blocked or !client.connection.wakeReady()) continue;
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
            // The idle timeout does not apply while a statement waits.
            if (client.blocked) client.touched = now;
            if (now - client.touched >= @as(i64, options.idle_ms) * 1000000) {
                client.close();
                continue;
            }
            if (client.serve()) buffered = true;
            if (client.socket == tcp.invalid_socket) continue;
            sockets[index] = client.socket;
            readable[index] = client.output_len == 0 and client.input_len < client.input.len;
            writable[index] = client.output_len != 0;
        }
        for (1..count) |index| {
            sockets[capacity + index - 1] = listeners[index];
            readable[capacity + index - 1] = true;
        }
        // Poll timeout: the idle bound or the nearest parked-statement timer.
        var timeout: u32 = @min(options.idle_ms, 1000);
        for (&overflow_cancels) |*pending| if (pending.socket != tcp.invalid_socket) { timeout = @min(timeout,10); break; };
        for (clients[0..options.max_clients]) |*client| {
            if (client.socket == tcp.invalid_socket or !client.blocked) continue;
            if (client.connection.wakeReady()) {
                timeout = 0;
                continue;
            }
            const due = client.connection.nextDeadline() orelse continue;
            const remaining = due - now;
            const ms: u32 = if (remaining <= 0) 0 else @intCast(@min(@divFloor(remaining + 999999, 1000000), timeout));
            timeout = @min(timeout, ms);
        }
        listener_ready[0] = try tcp.waitMasked(listeners[0], &sockets, &readable, &writable, if (buffered) 0 else timeout);
        for (1..count) |index| listener_ready[index] = readable[capacity + index - 1];
        for (clients[0..options.max_clients], 0..) |*client, index| {
            if (client.socket == tcp.invalid_socket) continue;
            if (writable[index]) {
                client.flush() catch {
                    client.close();
                    continue;
                };
            }
            if (client.socket == tcp.invalid_socket) continue;
            if (readable[index] and client.output_len == 0) {
                // Compact once per socket read, not once per pipelined packet.
                if (client.input_start != 0) {
                    for (0..client.input_len) |offset| client.input[offset] = client.input[client.input_start + offset];
                    client.input_start = 0;
                }
                if (tcp.recv(client.socket, client.input[client.input_len..]) catch {
                    client.close();
                    continue;
                }) |received| {
                    if (received == 0) {
                        client.close();
                        continue;
                    }
                    client.input_len += received;
                    client.touched = sig.os.clockMonotonicNs();
                }
            }
        }
    }
}

/// Offline backup validates/replays the existing source before publishing a copy.
pub fn backup(options: Options, target: []const u8) !void {
    if (options.memory) return error.InvalidServerOptions;
    try initializeMemory(options);
    var backing:owned_pages.Region=.{};
    try openStorage(options,&backing);
    defer backing.close() catch @panic("lost backup storage ownership");
    var workspace:owned_pages.Region=.{};
    try workspace.reserve(&memory_ledger,(try storagePlan(options)).workspace_bytes);
    defer workspace.close() catch @panic("lost backup workspace ownership");
    try store.database.bindWorkspaceMemory(workspace.memory.bytes, options.work_rows);
    _ = try persistence.initExisting(options.data, &store, &recovery_buffer);
    defer persistence.close();
    try @import("backup.sig").create(&persistence.file, target, &recovery_buffer);
}
