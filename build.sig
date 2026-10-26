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
    if (std.mem.eql(u8, name, "ramsql")) try cmd.appendArg("--demo");
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
    var wall_clock_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    const wall_clock_path = try zpm.modulePath("src/platform/wall_clock.sig", &wall_clock_buf);
    _ = try ctx.addModule("wall_clock", wall_clock_path);
    var pages_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    const pages_path = try zpm.modulePath("src/platform/page_region.sig", &pages_buf);
    _ = try ctx.addModule("page_region", pages_path);
    const pg_module = try ctx.addModule("postgres", pg_path);
    try ctx.addImport(pg_module, "scram", scram_path);
    const clients = ctx.option(usize, "clients", "Preallocated connection slots (1..60)") orelse 16;
    const rows = ctx.option(usize, "rows", "Rows per table") orelse 512;
    const tables = ctx.option(usize, "tables", "Table capacity") orelse 8;
    const columns = ctx.option(usize, "columns", "Columns per table/result (1..255)") orelse 8;
    const result_rows = ctx.option(usize, "result-rows", "Maximum rows returned by one statement") orelse rows;
    const row_locks = ctx.option(usize, "row-locks", "Shared row-lock entries") orelse 256;
    const predicates = ctx.option(usize, "predicates", "Boolean leaf predicates per WHERE") orelse 8;
    const expr_nodes = ctx.option(usize, "expr-nodes", "Expression nodes per statement") orelse 512;
    const relations = ctx.option(usize, "relations", "FROM items per query block") orelse 4;
    const text_bytes = ctx.option(usize, "text-bytes", "Bytes per text value") orelse 128;
    const transaction_bytes = ctx.option(usize, "transaction-bytes", "Change-record encode and recovery buffer bytes per transaction") orelse 1048576;
    const tx_writes = ctx.option(usize, "tx-writes", "Write-set entries (rows changed) per transaction") orelse 4096;
    const versions = ctx.option(usize, "versions", "Row-version slots per table (must exceed rows)") orelse rows * 2;
    const timezones = ctx.option(usize, "timezones", "Cached named time zones (16 KiB each)") orelse 8;
    const work_rows = ctx.option(usize, "work-rows", "Query workspace rows for sorts, groups, joins and CTEs (2 x 8 cells each)") orelse rows;
    const summary_groups = ctx.option(usize, "summary-groups", "Optional exact committed groups per supported column (0 disables group synopses; SQL falls back beyond budget)") orelse 64;
    const compact_integral_scans = ctx.option(bool, "compact-integral-scans", "Maintain caller-backed committed integer columns for analytical scans") orelse true;
    if (clients == 0 or clients > 60 or rows == 0 or tables == 0 or text_bytes == 0 or text_bytes > 65535 or transaction_bytes < 65540 or tx_writes == 0 or versions <= rows or timezones == 0 or timezones > 256 or work_rows == 0) return error.BufferTooSmall;
    if (columns == 0 or columns > 255 or result_rows == 0 or row_locks == 0 or predicates == 0 or expr_nodes == 0 or relations == 0) return error.BufferTooSmall;
    var profile_buf: [2048]u8 = undefined;
    const profile = try std.fmt.bufPrint(&profile_buf, "pub const clients={d}; pub const rows={d}; pub const tables={d}; pub const text_bytes={d}; pub const transaction_bytes={d}; pub const tx_writes={d}; pub const versions={d}; pub const transactions={d}; pub const timezones={d}; pub const work_rows={d}; pub const summary_groups={d}; pub const compact_integral_scans={s};\n", .{ clients, rows, tables, text_bytes, transaction_bytes, tx_writes, versions, clients + 2, timezones, work_rows, summary_groups, if(compact_integral_scans) "true" else "false" });
    var extra_buf: [512]u8 = undefined;
    const extra = try std.fmt.bufPrint(&extra_buf, "pub const columns={d}; pub const result_rows={d}; pub const row_locks={d}; pub const predicates={d}; pub const expr_nodes={d}; pub const relations={d};\n", .{ columns, result_rows, row_locks, predicates, expr_nodes, relations });
    @memcpy(profile_buf[profile.len..][0..extra.len], extra);
    const full_profile = profile_buf[0..profile.len + extra.len];
    var profile_path_buf: [256]u8 = undefined;
    const profile_path = try std.fmt.bufPrint(&profile_path_buf, ".sig-cache/profile-{x}.sig", .{std.hash.Wyhash.hash(0, full_profile)});
    try sig_build.sig_io.Dir.cwd().createDirPath(.{}, ".sig-cache");
    const profile_file = try sig_build.sig_io.Dir.cwd().createFile(.{}, profile_path, .{});
    try profile_file.writeStreamingAll(.{}, full_profile);
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
            importEntry("wall_clock", wall_clock_path),
            importEntry("profile", profile_path),
            importEntry("page_region", pages_path),
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
    const review_bench = try ctx.addCompileStep(.{
        .source_path = "src/review_benchmark.sig", .output_name = "ramsql-review-bench",
        .cache_dir = ctx.cache_dir[0..ctx.cache_dir_len], .optimize = ctx.optimize,
        .target = null, .imports = &.{ importEntry("sql", sql_path), importEntry("sig", runtime_path), importEntry("page_region", pages_path), importEntry("profile", profile_path) }, .compiler_path = "",
    });
    const review_step = try ctx.addStep("benchmark-review", "ramsql-review-bench", &runBinary);
    try ctx.addDependency(review_step, review_bench);
    _ = try ctx.addCompileStep(.{
        .source_path = "src/production_benchmark.sig", .output_name = "ramsql-production-bench",
        .cache_dir = ctx.cache_dir[0..ctx.cache_dir_len], .optimize = ctx.optimize,
        .target = null, .imports = &.{ importEntry("sql", sql_path), importEntry("sig", runtime_path), importEntry("page_region", pages_path), importEntry("db_file", db_file_path), importEntry("profile", profile_path) }, .compiler_path = "",
    });
    const benchmark = try ctx.addStep("benchmark", "ramsql-bench", &runBinary);
    try ctx.addDependency(benchmark, bench);
    var test_buf: [sig_build.PATH_BUF_SIZE]u8 = undefined;
    inline for (.{ "postgres", "mysql", "tds", "db_store", "tcp_server", "sql_index", "scram", "durable_log", "db_file", "entropy", "db_stream", "pg_stream", "sql_storage", "sql_change_log", "sql_expr", "sql_mvcc", "sql_types", "sql_query", "sql_stream_group", "sql_integer_topk", "sql_layout_order", "sql_summary", "sql_dense_group", "sql_rollup", "sql_ordered_probes", "sql_compact_scan", "sql_dml", "sql_catalog", "wall_clock", "pg_copy" }) |name| {
        const path = try zpm.modulePath("tests/test_" ++ name ++ ".sig", &test_buf);
        const compiled = try ctx.addCompileStep(.{
            .source_path = path,
            .output_name = "contract-" ++ name,
            .cache_dir = ctx.cache_dir[0..ctx.cache_dir_len],
            .optimize = ctx.optimize,
            .target = null,
            .imports = &.{ importEntry("sql", sql_path), importEntry("postgres", pg_path), importEntry("mysql", mysql_path), importEntry("tds", tds_path), importEntry("db_store", store_path), importEntry("scram", scram_path), importEntry("durable_log", durable_log_path), importEntry("db_file", db_file_path), importEntry("entropy", entropy_path), importEntry("stream_server", stream_path), importEntry("profile", profile_path), importEntry("tcp_server", tcp_path), importEntry("wall_clock", wall_clock_path) },
            .compiler_path = "",
        });
        const contract = try ctx.addStep("test-" ++ name, "contract-" ++ name, &runBinary);
        try ctx.addDependency(contract, compiled);
        try ctx.addDependency(test_step, contract);
    }
}
