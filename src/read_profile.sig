//! Checked native read-path phase measurements; excludes sockets and scheduling.
const sql=@import("sql");
const sig=@import("sig");
const pages=@import("page_region");
const p=@import("profile");
const DB=sql.Database(.{.external_storage=true,.external_workspace=true,.external_results=true,
    .tables=p.tables,.columns=p.columns,.rows_per_table=p.rows,.text_bytes=p.text_bytes,
    .expr_nodes=p.expr_nodes,.result_rows=p.result_rows,.work_rows=p.work_rows,
    .tx_writes=p.tx_writes,.transactions=p.transactions,.summary_groups=p.summary_groups,
    .compact_integral_scans=p.compact_integral_scans});
var db:DB=.{};
var context:DB.ReadContext=.{};
var result:DB.Result=.{};
const count=10000;
const query="SELECT id,value FROM lookup WHERE id=511";
fn checked() !void {
    if(result.row_count!=1 or result.column_count!=2 or result.rows[0].cells[0]!=.integer or
        result.rows[0].cells[0].integer!=511 or result.rows[0].cells[1]!=.integer or
        result.rows[0].cells[1].integer!=1533) return error.IncorrectRead;
}
pub fn main() !void {
    var storage=try pages.reserve(try DB.backingBytes(8192,16384,16384));defer storage.close();
    try db.bindMemory(storage.bytes,8192,16384,16384);
    var workspace=try pages.reserve(try DB.queryWorkspaceBytes(4096));defer workspace.close();
    try db.bindWorkspace(try workspace.view(DB.Cell,0,4096*2*p.columns));
    var scratch=try pages.reserve(try DB.ReadContext.backingBytesFor(4096,16384));defer scratch.close();
    try context.bindMemoryFor(scratch.bytes,4096,16384);
    var output=try pages.reserve(try DB.Result.rowBytes(32));defer output.close();
    try result.bindMemory(output.bytes,32);
    try db.execute("CREATE TABLE lookup(id INT PRIMARY KEY,value INT)",&result);
    var text:[128]u8=undefined;
    for(0..512) |id| try db.execute(try sig.fmt.bufPrint(&text,"INSERT INTO lookup VALUES({d},{d})",.{id,id*3}),&result);
    const snapshot:DB.Snapshot=.{.csn=db.csn};
    for(0..1000) |_| {try db.execute(query,&result);try context.execute(&db,query,snapshot,&result);try checked();}
    const start=sig.os.clockMonotonicNs();
    for(0..count) |_| {try db.execute(query,&result);try checked();}
    const legacy=sig.os.clockMonotonicNs()-start;
    var prepare:i64=0;var run:i64=0;
    for(0..count) |_| {
        const began=sig.os.clockMonotonicNs();
        try context.prepare(&db,query,snapshot);
        const bound=sig.os.clockMonotonicNs();
        try context.run(&result);
        const done=sig.os.clockMonotonicNs();
        prepare+=bound-began;run+=done-bound;try checked();
    }
    var message:[512]u8=undefined;
    try sig.io.File.stdout().writeStreamingAll(.{},try sig.fmt.bufPrint(&message,
        "{{\"schema\":1,\"scope\":\"native_read_phases\",\"iterations\":{d},\"legacy_ns\":{d},\"prepare_ns\":{d},\"run_ns\":{d},\"correctness_checked\":true,\"timing_boundary\":\"prepare/run include phase clock overhead; excludes sockets and scheduling\"}}\n",
        .{count,legacy,prepare,run}));
}
