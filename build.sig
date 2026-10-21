// Native build graph, extended from `zpm init --template package`.
const sig_build = @import("sig_build");
const builtin = @import("builtin");

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
}
