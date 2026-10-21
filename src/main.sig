//! RAMSQL CLI. All database and I/O storage has a fixed owner and bound.
const std = @import("std");
const ram = @import("ramsql");
const sig = @import("sig");
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

fn run(init: std.process.Init.Minimal) !u8 {
    var args_buf: [8192]u8 = undefined;
    var args = process.Argv_Iterator.init(init.args.vector, &args_buf);
    _ = try args.next();
    if (try args.next()) |arg| {
        // Choose the mode before next() reuses the argument buffer.
        const mode: enum { help, version, input, demo, invalid } =
            if (same(arg, "--help")) .help else if (same(arg, "--version")) .version else if (same(arg, "--stdin")) .input else if (same(arg, "--demo")) .demo else .invalid;
        if ((try args.next()) != null or mode == .invalid) {
            report("UnknownArgument");
            return 2;
        }
        switch (mode) {
            .help => try write("ramsql 0.1.0\nUsage: ramsql [--demo|--stdin|--version|--help]\n--stdin: one SQL statement per line; database lasts for this process.\n"),
            .version => try write("ramsql " ++ ram.version ++ "\n"),
            .input => return if (try stdin()) 1 else 0,
            .demo => try demo(),
            .invalid => unreachable,
        }
    } else try demo();
    return 0;
}

pub fn main(init: std.process.Init.Minimal) void {
    const status = run(init) catch |err| {
        report(@errorName(err));
        process.exit(1);
    };
    process.exit(status);
}
