// Native build graph, extended from `zpm init --template package`.
const sig_build = @import("sig_build");
const builtin = @import("builtin");
const std = @import("std");

fn noop(_: *sig_build.Step_Context) sig_build.SigError!void {}

fn importEntry(name: []const u8, path: []const u8) sig_build.Import_Entry {
    var entry: sig_build.Import_Entry = .{};
    @memcpy(entry.name[0..name.len], name);
    entry.name_len = name.len;
    @memcpy(entry.path[0..path.len], path);
    entry.path_len = path.len;
    return entry;
}

fn runBinary(ctx: *sig_build.Step_Context) sig_build.SigError!void {
    const entry = &ctx.build_ctx.steps.entries[ctx.step_handle];
    const name = entry.desc[0..entry.desc_len];
    const prefix = ctx.build_ctx.install_prefix[0..ctx.build_ctx.install_prefix_len];
    const suffix = if (builtin.os.tag == .windows) ".exe" else "";
    var path: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    const len = prefix.len + 5 + name.len + suffix.len;
    if (len > path.len) return error.BufferTooSmall;
    @memcpy(path[0..prefix.len], prefix);
    @memcpy(path[prefix.len..][0..5], "/bin/");
    @memcpy(path[prefix.len + 5 ..][0..name.len], name);
    @memcpy(path[prefix.len + 5 + name.len ..][0..suffix.len], suffix);
    var cmd: sig_build.Command_Buffer = .{};
    try cmd.appendArg(path[0..len]);
    try cmd.setCwd(ctx.build_ctx.build_root[0..ctx.build_ctx.build_root_len]);
    var errors: [sig_build.STDERR_CAPTURE_SIZE]u8 = undefined;
    var errors_len: usize = 0;
    const status = try sig_build.runCommand(&cmd, &errors, &errors_len, ctx.io);
    if (errors_len != 0) sig_build.printMsg(ctx.io, "{s}", .{errors[0..errors_len]});
    if (status != 0) return error.BufferTooSmall;
}

fn libPath(base: []const u8, suffix: []const u8, buf: []u8) ![]const u8 {
    if (base.len + suffix.len > buf.len) return error.BufferTooSmall;
    @memcpy(buf[0..base.len], base);
    @memcpy(buf[base.len..][0..suffix.len], suffix);
    return buf[0 .. base.len + suffix.len];
}

pub fn build(ctx: *sig_build.Build_Context) !void {
    const zpm = ctx.getDependency("zpm") orelse return error.BufferTooSmall;
    var sql_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    const sql_path = try zpm.modulePath("src/core/sql.sig", &sql_buf);
    _ = try ctx.addModule("sql", sql_path);
    var pg_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    var mysql_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    var tds_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    var tcp_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    var store_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    const pg_path = try zpm.modulePath("src/db/postgres.sig", &pg_buf);
    const mysql_path = try zpm.modulePath("src/db/mysql.sig", &mysql_buf);
    const tds_path = try zpm.modulePath("src/db/tds.sig", &tds_buf);
    const tcp_path = try zpm.modulePath("src/platform/tcp_server.sig", &tcp_buf);
    const store_path = try zpm.modulePath("src/db/store.sig", &store_buf);
    var scram_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    const scram_path = try zpm.modulePath("src/db/scram.sig", &scram_buf);
    _ = try ctx.addModule("scram", scram_path);
    var durable_log_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    const durable_log_path = try zpm.modulePath("src/db/durable_log.sig", &durable_log_buf);
    _ = try ctx.addModule("durable_log", durable_log_path);
    var db_file_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    const db_file_path = try zpm.modulePath("src/platform/db_file.sig", &db_file_buf);
    _ = try ctx.addModule("db_file", db_file_path);
    var entropy_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    const entropy_path = try zpm.modulePath("src/platform/entropy.sig", &entropy_buf);
    _ = try ctx.addModule("entropy", entropy_path);
    var stream_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    const stream_path = try zpm.modulePath("src/db/stream_server.sig", &stream_buf);
    _ = try ctx.addModule("stream_server", stream_path);
    const pg_module = try ctx.addModule("postgres", pg_path);
    try ctx.addImport(pg_module, "scram", scram_path);
    const clients = ctx.option(usize, "clients", "Preallocated connection slots (1..60)") orelse 16;
    const rows = ctx.option(usize, "rows", "Rows per table") orelse 512;
    const tables = ctx.option(usize, "tables", "Table capacity") orelse 8;
    const text_bytes = ctx.option(usize, "text-bytes", "Bytes per text value") orelse 128;
    const transaction_bytes = ctx.option(usize, "transaction-bytes", "SQL log bytes per transaction") orelse 1048576;
    if (clients == 0 or clients > 60 or rows == 0 or tables == 0 or text_bytes == 0 or transaction_bytes < 65540) return error.BufferTooSmall;
    var profile_buf: [512]u8 = undefined;
    const profile = try std.fmt.bufPrint(&profile_buf, "pub const clients={d}; pub const rows={d}; pub const tables={d}; pub const text_bytes={d}; pub const transaction_bytes={d};\n", .{ clients, rows, tables, text_bytes, transaction_bytes });
    var profile_path_buf: [256]u8 = undefined;
    const profile_path = try std.fmt.bufPrint(&profile_path_buf, ".sig-cache/profile-{d}-{d}-{d}-{d}-{d}.sig", .{ clients, rows, tables, text_bytes, transaction_bytes });
    try sig_build.sig_io.Dir.cwd().createDirPath(.{}, ".sig-cache");
    const profile_file = try sig_build.sig_io.Dir.cwd().createFile(.{}, profile_path, .{});
    try profile_file.writeStreamingAll(.{}, profile);
    profile_file.close(.{});
    _ = try ctx.addModule("profile", profile_path);
    const lib = try ctx.addModule("ramsql", "src/root.sig");
    try ctx.addImport(lib, "sql", sql_path);
    const sig_lib = ctx.sig_lib_dir[0..ctx.sig_lib_dir_len];
    var runtime_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    const app = try ctx.addCompileStep(.{
        .source_path = "src/main.sig",
        .output_name = "ramsql",
        .cache_dir = ctx.cache_dir[0..ctx.cache_dir_len],
        .optimize = ctx.optimize,
        .target = if (ctx.target.arch_len > 0) &ctx.target else null,
        .imports = &.{
            importEntry("ramsql", "src/root.sig"),
            importEntry("sig", try libPath(sig_lib, "/sig/sig.sig", &runtime_buf)),
            importEntry("sql", sql_path),
            importEntry("postgres", pg_path),
            importEntry("mysql", mysql_path),
            importEntry("tds", tds_path),
            importEntry("tcp_server", tcp_path),
            importEntry("db_store", store_path),
            importEntry("scram", scram_path),
            importEntry("durable_log", durable_log_path),
            importEntry("db_file", db_file_path),
            importEntry("entropy", entropy_path),
            importEntry("profile", profile_path),
        },
        .compiler_path = "",
    });
    const install = try ctx.addStep("install", "Build RAMSQL CLI", &noop);
    try ctx.addDependency(install, app);
    const run = try ctx.addStep("run", "ramsql", &runBinary);
    try ctx.addDependency(run, app);

    // Compile executable contracts so success requires assertions to execute.
    var contract_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    const engine = try ctx.addCompileStep(.{
        .source_path = try zpm.modulePath("tests/test_sql.sig", &contract_buf),
        .output_name = "contract-sql",
        .cache_dir = ctx.cache_dir[0..ctx.cache_dir_len],
        .optimize = ctx.optimize,
        .target = null,
        .imports = &.{importEntry("sql", sql_path)},
        .compiler_path = "",
    });
    const test_step = try ctx.addStep("test", "Run engine and public API contracts", &noop);
    const test_engine = try ctx.addStep("test-engine", "contract-sql", &runBinary);
    try ctx.addDependency(test_engine, engine);
    try ctx.addDependency(test_step, test_engine);
    const persistence_module = try ctx.addModule("persistence", "src/persistence.sig");
    try ctx.addImport(persistence_module, "db_file", db_file_path);
    try ctx.addImport(persistence_module, "durable_log", durable_log_path);
    try ctx.addImport(persistence_module, "db_store", store_path);
    const backup_module = try ctx.addModule("backup", "src/backup.sig");
    try ctx.addImport(backup_module, "db_file", db_file_path);
    const backup_test = try ctx.addCompileStep(.{
        .source_path = "tests/backup.sig",
        .output_name = "contract-backup",
        .cache_dir = ctx.cache_dir[0..ctx.cache_dir_len],
        .optimize = ctx.optimize,
        .target = null,
        .imports = &.{ importEntry("sql", sql_path), importEntry("db_store", store_path), importEntry("db_file", db_file_path), importEntry("durable_log", durable_log_path), importEntry("persistence", "src/persistence.sig"), importEntry("backup", "src/backup.sig") },
        .compiler_path = "",
    });
    const test_backup = try ctx.addStep("test-backup", "contract-backup", &runBinary);
    try ctx.addDependency(test_backup, backup_test);
    try ctx.addDependency(test_step, test_backup);
    const persistence_test = try ctx.addCompileStep(.{
        .source_path = "tests/persistence.sig",
        .output_name = "contract-persistence",
        .cache_dir = ctx.cache_dir[0..ctx.cache_dir_len],
        .optimize = ctx.optimize,
        .target = null,
        .imports = &.{ importEntry("sql", sql_path), importEntry("db_store", store_path), importEntry("db_file", db_file_path), importEntry("durable_log", durable_log_path), importEntry("persistence", "src/persistence.sig") },
        .compiler_path = "",
    });
    const test_persistence = try ctx.addStep("test-persistence", "contract-persistence", &runBinary);
    try ctx.addDependency(test_persistence, persistence_test);
    try ctx.addDependency(test_step, test_persistence);
    const api = try ctx.addCompileStep(.{
        .source_path = "tests/api.sig",
        .output_name = "contract-ramsql",
        .cache_dir = ctx.cache_dir[0..ctx.cache_dir_len],
        .optimize = ctx.optimize,
        .target = null,
        .imports = &.{importEntry("ramsql", "src/root.sig")},
        .compiler_path = "",
    });
    const test_api = try ctx.addStep("test-api", "contract-ramsql", &runBinary);
    try ctx.addDependency(test_api, api);
    try ctx.addDependency(test_step, test_api);
    const runtime_path = try libPath(sig_lib, "/sig/sig.sig", &runtime_buf);
    const bench = try ctx.addCompileStep(.{
        .source_path = "src/benchmark.sig",
        .output_name = "ramsql-bench",
        .cache_dir = ctx.cache_dir[0..ctx.cache_dir_len],
        .optimize = ctx.optimize,
        .target = null,
        .imports = &.{ importEntry("sql", sql_path), importEntry("sig", runtime_path) },
        .compiler_path = "",
    });
    const benchmark = try ctx.addStep("benchmark", "ramsql-bench", &runBinary);
    try ctx.addDependency(benchmark, bench);
    var test_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    inline for (.{ "postgres", "mysql", "tds", "db_store", "tcp_server", "sql_index", "scram", "durable_log", "db_file", "entropy", "db_stream", "pg_stream" }) |name| {
        const path = try zpm.modulePath("tests/test_" ++ name ++ ".sig", &test_buf);
        const compiled = try ctx.addCompileStep(.{
            .source_path = path,
            .output_name = "contract-" ++ name,
            .cache_dir = ctx.cache_dir[0..ctx.cache_dir_len],
            .optimize = ctx.optimize,
            .target = null,
            .imports = &.{ importEntry("sql", sql_path), importEntry("postgres", pg_path), importEntry("mysql", mysql_path), importEntry("tds", tds_path), importEntry("db_store", store_path), importEntry("scram", scram_path), importEntry("durable_log", durable_log_path), importEntry("db_file", db_file_path), importEntry("entropy", entropy_path), importEntry("stream_server", stream_path), importEntry("profile", profile_path), importEntry("tcp_server", tcp_path) },
            .compiler_path = "",
        });
        const contract = try ctx.addStep("test-" ++ name, "contract-" ++ name, &runBinary);
        try ctx.addDependency(contract, compiled);
        try ctx.addDependency(test_step, contract);
    }
}
