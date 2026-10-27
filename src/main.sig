//! RAMSQL CLI. All database and I/O storage has a fixed owner and bound.
const std = @import("std");
const ram = @import("ramsql");
const sig = @import("sig");
const server = @import("server.sig");
const io = sig.io;
const fmt = sig.fmt;
const process = sig.process;

var database: ram.DefaultDatabase = .{};
var result: ram.DefaultDatabase.Result = .{};

fn write(bytes: []const u8) !void {
    try io.File.stdout().writeStreamingAll(.{}, bytes);
}

fn report(name: []const u8) void {
    io.File.stderr().writeStreamingAll(.{}, "error: ") catch {};
    io.File.stderr().writeStreamingAll(.{}, name) catch {};
    io.File.stderr().writeStreamingAll(.{}, "\n") catch {};
}

fn number(value: anytype) !void {
    var buf: [32]u8 = undefined;
    try write(try fmt.bufPrint(&buf, "{d}", .{value}));
}

fn text(bytes: []const u8) !void {
    var start: usize = 0;
    for (bytes, 0..) |c, i| {
        const escaped: ?[]const u8 = switch (c) {
            '\t' => "\\t",
            '\r' => "\\r",
            '\n' => "\\n",
            '\\' => "\\\\",
            else => null,
        };
        if (escaped) |replacement| {
            try write(bytes[start..i]);
            try write(replacement);
            start = i + 1;
        }
    }
    try write(bytes[start..]);
}

fn execute(statement: []const u8) !void {
    try database.execute(statement, &result);
    if (result.column_count == 0) {
        try write("OK ");
        try number(result.affected_rows);
        try write("\n");
        return;
    }
    for (0..result.column_count) |i| {
        if (i != 0) try write("\t");
        try write(result.column_names[i].slice());
    }
    try write("\n");
    for (result.rows[0..result.row_count]) |*row| {
        for (row.cells[0..result.column_count], 0..) |*cell, i| {
            if (i != 0) try write("\t");
            switch (cell.*) {
                .null_value => try write("NULL"),
                .integer => |value| try number(value),
                .boolean => |value| try write(if (value) "TRUE" else "FALSE"),
                .text => |*value| try text(value.slice()),
                // NUMERIC, REAL/DOUBLE, temporal and JSONB: the SQL output function.
                else => {
                    var buffer: [ram.DefaultDatabase.Io.format_bytes]u8 = undefined;
                    try text(try ram.DefaultDatabase.Io.format(cell, false, &database.session, &buffer));
                },
            }
        }
        try write("\n");
    }
}

fn blank(line: []const u8) bool {
    for (line) |c| if (c != ' ' and c != '\r' and c != '\t') return false;
    return true;
}

fn stdin() !bool {
    var reader = io.File.stdin().reader(.{}, &.{});
    var input: [512]u8 = undefined;
    var line: [4096]u8 = undefined;
    var len: usize = 0;
    var overflow = false;
    var failed = false;
    while (true) {
        const count = try reader.interface.readSliceShort(&input);
        if (count == 0) break;
        for (input[0..count]) |c| {
            if (c == '\n') {
                if (overflow) {
                    report("StatementTooLong");
                    failed = true;
                } else if (!blank(line[0..len])) {
                    execute(line[0..len]) catch |err| {
                        report(@errorName(err));
                        failed = true;
                    };
                }
                len = 0;
                overflow = false;
            } else if (!overflow) {
                if (len == line.len) overflow = true else {
                    line[len] = c;
                    len += 1;
                }
            }
        }
    }
    if (overflow) {
        report("StatementTooLong");
        failed = true;
    } else if (!blank(line[0..len])) {
        execute(line[0..len]) catch |err| {
            report(@errorName(err));
            failed = true;
        };
    }
    return failed;
}

fn demo() !void {
    try execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL, active BOOLEAN)");
    try execute("INSERT INTO projects VALUES (1, 'ramsql', TRUE)");
    try execute("INSERT INTO projects VALUES (2, 'sig', TRUE)");
    try execute("SELECT id, name FROM projects WHERE active = TRUE ORDER BY id");
}

fn same(a: []const u8, b: []const u8) bool {
    if (a.len != b.len) return false;
    for (a, b) |x, y| if (x != y) return false;
    return true;
}

fn budgets(source: []const u8) ![server.table_capacity]usize {
    var values: [server.table_capacity]usize = undefined;
    var start: usize = 0; var used: usize = 0;
    for (0..source.len+1) |position| {
        if (position != source.len and source[position] != ',') continue;
        if (used == values.len) return error.InvalidServerOptions;
        values[used] = try fmt.parseInt(usize,std.mem.trim(u8,source[start..position]," "),10);
        used+=1; start=position+1;
    }
    if (used != values.len) return error.InvalidServerOptions;
    return values;
}

fn run(init: std.process.Init.Minimal) !u8 {
    var args_buf: [8192]u8 = undefined;
    var args = process.Argv_Iterator.init(init.args.vector, &args_buf);
    _ = try args.next();
    var options: server.Options = .{};
    var data_buffer: [4096]u8 = undefined;
    var verifier_buffer: [4096]u8 = undefined;
    var user_buffer: [64]u8 = undefined;
    var backup_buffer: [4096]u8 = undefined;
    var timezone_buffer: [4096]u8 = undefined;
    var backup_target: ?[]const u8 = null;
    var data_set = false;
    var mode: enum { serve, demo, input, help, version, capabilities, machine_capacity } = .serve;
    var explicit_mode = false;
    var has_server_options = false;
    while (try args.next()) |arg| {
        if (same(arg, "--serve") or same(arg, "--demo") or same(arg, "--stdin") or same(arg, "--help") or same(arg, "--version") or same(arg, "--capabilities") or same(arg,"--machine-capacity")) {
            if (explicit_mode) return error.UnknownArgument;
            explicit_mode = true;
            mode = if (same(arg, "--serve")) .serve else if (same(arg, "--demo")) .demo else if (same(arg, "--stdin")) .input else if (same(arg, "--help")) .help else if (same(arg, "--capabilities")) .capabilities else if(same(arg,"--machine-capacity")) .machine_capacity else .version;
        } else if (same(arg, "--memory")) {
            if (data_set) return error.InvalidServerOptions;
            options.memory = true;
            has_server_options = true;
        } else if (same(arg, "--insecure-local")) {
            options.insecure_local = true;
            has_server_options = true;
        } else {
            const option: enum { protocol, port, mysql_port, mssql_port, max_clients, workers, idle_ms, max_query_work, storage_rows, storage_versions, work_rows, result_rows, table_rows, table_versions, data, verifier_file, auth_user, backup_to, timezone_dir, memory_budget, os_headroom } =
                if(same(arg,"--memory-budget")) .memory_budget else if(same(arg,"--os-headroom")) .os_headroom else if (same(arg, "--workers")) .workers else if (same(arg, "--table-rows")) .table_rows else if (same(arg, "--table-versions")) .table_versions else if (same(arg, "--work-rows")) .work_rows else if (same(arg, "--result-rows")) .result_rows else if (same(arg, "--rows")) .storage_rows else if (same(arg, "--versions")) .storage_versions else if (same(arg, "--protocol")) .protocol else if (same(arg, "--port")) .port else if (same(arg, "--mysql-port")) .mysql_port else if (same(arg, "--mssql-port")) .mssql_port else if (same(arg, "--max-clients")) .max_clients else if (same(arg, "--max-query-work")) .max_query_work else if (same(arg, "--idle-ms")) .idle_ms else if (same(arg, "--data")) .data else if (same(arg, "--auth-verifier-file")) .verifier_file else if (same(arg, "--auth-user")) .auth_user else if (same(arg, "--backup-to")) .backup_to else if (same(arg, "--timezone-dir")) .timezone_dir else return error.UnknownArgument;
            const value = try args.next() orelse return error.UnknownArgument;
            has_server_options = true;
            switch (option) {
                .backup_to => {
                    if (value.len == 0 or value.len > backup_buffer.len or backup_target != null) return error.InvalidServerOptions;
                    @memcpy(backup_buffer[0..value.len], value);
                    backup_target = backup_buffer[0..value.len];
                },
                .data => {
                    if (options.memory or value.len == 0 or value.len > data_buffer.len) return error.InvalidServerOptions;
                    @memcpy(data_buffer[0..value.len], value);
                    options.data = data_buffer[0..value.len];
                    data_set = true;
                },
                .verifier_file => {
                    if (value.len == 0 or value.len > verifier_buffer.len) return error.InvalidServerOptions;
                    @memcpy(verifier_buffer[0..value.len], value);
                    options.auth_verifier_file = verifier_buffer[0..value.len];
                },
                .timezone_dir => {
                    if (value.len == 0 or value.len > timezone_buffer.len) return error.InvalidServerOptions;
                    @memcpy(timezone_buffer[0..value.len], value);
                    options.timezone_dir = timezone_buffer[0..value.len];
                },
                .auth_user => {
                    if (value.len == 0 or value.len > user_buffer.len) return error.InvalidServerOptions;
                    @memcpy(user_buffer[0..value.len], value);
                    options.auth_user = user_buffer[0..value.len];
                },
                .protocol => options.protocol = if (same(value, "postgres")) .postgres else if (same(value, "mysql")) .mysql else if (same(value, "mssql")) .mssql else return error.UnknownArgument,
                .port => options.port = try fmt.parseInt(u16, value, 10),
                .mysql_port => options.mysql_port = try fmt.parseInt(u16, value, 10),
                .mssql_port => options.mssql_port = try fmt.parseInt(u16, value, 10),
                .max_clients => options.max_clients = try fmt.parseInt(usize, value, 10),
                .workers => options.workers = try fmt.parseInt(usize, value, 10),
                .memory_budget => {const amount=try fmt.parseInt(u64,value,10);if(amount==0) return error.InvalidServerOptions;options.memory_budget=amount;},
                .os_headroom => options.os_headroom = try fmt.parseInt(u64,value,10),
                .storage_rows => options.storage_rows = try fmt.parseInt(usize, value, 10),
                .storage_versions => options.storage_versions = try fmt.parseInt(usize, value, 10),
                .table_rows => options.table_rows = try budgets(value),
                .table_versions => options.table_versions = try budgets(value),
                .work_rows => options.work_rows = try fmt.parseInt(usize, value, 10),
                .result_rows => options.result_rows = try fmt.parseInt(usize, value, 10),
                .max_query_work => options.max_query_work = try fmt.parseInt(u64, value, 10),
                .idle_ms => options.idle_ms = try fmt.parseInt(u32, value, 10),
            }
        }
    }
    if (has_server_options and mode != .serve and mode != .capabilities and mode != .machine_capacity) return error.UnknownArgument;
    if (backup_target) |target| {
        if (mode != .serve or options.memory) return error.InvalidServerOptions;
        try server.backup(options, target);
        try write("RAMSQL backup complete\n");
        return 0;
    }
    switch (mode) {
        .help => try write("ramsql beta (controlled testing; not production)\nUsage: ramsql [--serve|--demo|--stdin|--version|--capabilities|--machine-capacity|--help]\nServer: --protocol postgres|mysql|mssql --port N --mysql-port N --mssql-port N\n        --max-clients N --workers N (experimental I/O reactors; SQL serialized) --idle-ms N --max-query-work N (0: unlimited); default loopback PostgreSQL:5432\nMachine: --machine-capacity (JSON native resources and byte plan; opens no database/listeners)\n         --memory-budget BYTES --os-headroom BYTES (also apply to server region admission)\nInspect: --capabilities (JSON; accepts runtime capacity options; opens no files/listeners)\nCapacity: --rows N --versions N --work-rows N --result-rows N (independent runtime budgets)\n          --table-rows n,n,... --table-versions n,n,... (one budget per table slot)\nStorage: --data WAL_PATH (default ramsql.wal) | --memory\nBackup: --data EXISTING_WAL --backup-to NEW_PATH (offline)\nAuth: --auth-user USER --auth-verifier-file PATH (PostgreSQL SCRAM verifier)\nTime zones: --timezone-dir ZONEINFO_DIR (default /usr/share/zoneinfo on Linux/macOS)\n      --insecure-local explicitly enables unauthenticated loopback testing\n--stdin: one SQL statement per line. --demo: run the example and exit.\n"),
        .version => try write("ramsql " ++ ram.version ++ "\n"),
        .capabilities => try server.describe(options),
        .machine_capacity => try server.describeMachine(options),
        .input => return if (try stdin()) 1 else 0,
        .demo => try demo(),
        .serve => try server.run(options),
    }
    return 0;
}

pub fn main(init: std.process.Init.Minimal) void {
    const status = run(init) catch |err| {
        report(@errorName(err));
        process.exit(if (err == error.UnknownArgument or err == error.InvalidCharacter or err == error.Overflow or err == error.InvalidServerOptions) 2 else 1);
    };
    process.exit(status);
}
