//! Portable trace replay through the public SQL API. No allocator or threads.
const std = @import("std");
const sig = @import("sig");
const sql = @import("sql");
const pages = @import("page_region");
const files = @import("db_file");
const profile = @import("profile");
const DB = sql.Database(.{ .external_storage=true,.tables=1,.columns=4,.rows_per_table=4,
    .text_bytes=profile.text_bytes,.result_rows=2048,.work_rows=profile.work_rows,.tx_writes=64,.integer_bits=32,.nulls_first_ascending=false,.summary_groups=64,.compact_integral_scans=profile.compact_integral_scans });
var db: DB=.{};
var result: DB.Result=.{};
const Request=struct { kind:u32, rows:u32, columns:u32, digest:u64, source:[]const u8 };
var requests:[65536]Request=undefined;
var checks:[16]Request=undefined;
const Reader=struct {
    bytes:[]const u8, at:usize=0,
    fn take(self:*Reader,n:usize) ![]const u8 {
        if(n>self.bytes.len-self.at) return error.TruncatedTrace;
        const bytes=self.bytes[self.at..][0..n];self.at+=n;return bytes;
    }
    fn number(self:*Reader,comptime T:type) !T {return std.mem.readInt(T,(try self.take(@sizeOf(T)))[0..@sizeOf(T)],.little);}
    fn request(self:*Reader) !Request {
        const kind=try self.number(u32);const n=try self.number(u32);
        const rows=try self.number(u32);const columns=try self.number(u32);const digest=try self.number(u64);
        if((kind!=1 and kind!=2) or n==0 or n>65536 or rows>2048 or columns>4) return error.InvalidTrace;
        return .{.kind=kind,.rows=rows,.columns=columns,.digest=digest,.source=try self.take(n)};
    }
};
fn mix(h:u64,value:u64) u64 {return h*%1099511628211 ^ value;}
fn execute(request:Request) !void {
    // Flat reads/DML do not enter the aggregate workspace reset path.
    // Do not attribute the preceding physical post-check to those requests.
    db.workspace.query.stream_stats=.{};
    try db.execute(request.source,&result);
    if(request.kind==2) {
        if((result.command!=.update and result.command!=.insert and result.command!=.delete) or result.affected_rows!=request.rows) return error.WrongAffectedCount;
        return;
    }
    if(result.row_count!=request.rows or result.column_count!=request.columns) return error.WrongShape;
    var h=mix(result.row_count,result.column_count);
    for(result.rows[0..result.row_count]) |*row| for(row.cells[0..result.column_count]) |*cell| {
        switch(cell.*) {
            .null_value=>h=mix(h,0),
            .integer=>|value| h=mix(mix(h,1),@bitCast(value)),
            .text=>|*text| {h=mix(mix(h,2),text.slice().len);for(text.slice()) |byte| h=mix(h,byte);},
            else=>return error.UnsupportedResultType,
        }
    };
    if(h!=request.digest) return error.WrongDigest;
}
fn write(bytes:[]const u8) !void {try sig.io.File.stdout().writeStreamingAll(.{},bytes);}
fn percentile(hist:*const [64]u64,total:u64,percent:u64) u64 {
    const target=(total*percent+99)/100;var sum:u64=0;
    for(hist,0..) |count,bin| {sum+=count;if(sum>=target) return @as(u64,1)<<@as(u6,@intCast(bin));}
    return 0;
}
fn run(init:std.process.Init.Minimal) !void {
    var args_buf:[8192]u8=undefined;var args=sig.process.Argv_Iterator.init(init.args.vector,&args_buf);_=try args.next();
    const path_arg=(try args.next()) orelse return error.InvalidArguments;
    var path_buffer:[4096]u8=undefined;
    if(path_arg.len>path_buffer.len) return error.InvalidArguments;
    @memcpy(path_buffer[0..path_arg.len],path_arg);
    const path=path_buffer[0..path_arg.len];
    const minimum_ms=try sig.fmt.parseInt(u64,(try args.next()) orelse "200",10);
    if(minimum_ms==0 or minimum_ms>10000 or try args.next()!=null) return error.InvalidArguments;
    var file=try files.File.openReadOnly(path);defer file.close();
    const length=try file.length();if(length<36 or length>1024*1024*1024) return error.InvalidTrace;
    var input=try pages.reserve(@intCast(length));defer input.close();
    var loaded:usize=0;
    while(loaded<length) {
        const got=try file.readAt(loaded,input.bytes[loaded..@intCast(length)]);
        if(got==0) return error.TruncatedTrace;
        loaded+=got;
    }
    var reader:Reader=.{.bytes=input.bytes[0..@intCast(length)]};
    if(!std.mem.eql(u8,try reader.take(8),"RAMTRC01")) return error.InvalidTrace;
    const n=try reader.number(u32);const groups=try reader.number(u32);const width=try reader.number(u32);
    const count=try reader.number(u32);const check_count=try reader.number(u32);_=try reader.number(u64);
    if(n<64 or n>10000000 or groups==0 or groups>2048 or width==0 or width>512 or count==0 or count>requests.len or check_count==0 or check_count>checks.len) return error.InvalidTrace;
    if(width>profile.text_bytes) return error.PayloadExceedsBuildProfile;
    var memory=try pages.reserve(try DB.backingBytes(n,@as(usize,n)*2,@max(@as(usize,n)*4,2048*32)));defer memory.close();
    try db.bindMemory(memory.bytes,n,@as(usize,n)*2,@max(@as(usize,n)*4,2048*32));
    const load_start=sig.os.clockMonotonicNs();
    try db.execute("CREATE TABLE bench (id INT PRIMARY KEY,value INT,category INT,payload TEXT)",&result);
    var buffer:[4096]u8=undefined;
    for(0..n) |_| {
        const id=try reader.number(i32);const value=try reader.number(i32);const category=try reader.number(i32);const text_len=try reader.number(i32);
        if(text_len < -1 or text_len>@as(i32,@intCast(width))) return error.InvalidTrace;
        const text=if(text_len<0) "" else try reader.take(@intCast(text_len));
        for(text) |byte| if(byte<32 or byte>126 or byte=='\'') return error.InvalidSeedText;
        const source=if(text_len<0) try sig.fmt.bufPrint(&buffer,"INSERT INTO bench VALUES ({d},{d},{d},NULL)",.{id,value,category})
            else try sig.fmt.bufPrint(&buffer,"INSERT INTO bench VALUES ({d},{d},{d},'{s}')",.{id,value,category,text});
        try db.execute(source,&result);if(result.affected_rows!=1) return error.WrongSeedCount;
    }
    const load_ns=sig.os.clockMonotonicNs()-load_start;
    for(requests[0..count]) |*request| request.*=try reader.request();
    for(checks[0..check_count]) |*request| request.*=try reader.request();
    if(reader.at!=reader.bytes.len) return error.TrailingTraceBytes;
    for(checks[0..check_count]) |request| try execute(request);
    const warm_start=sig.os.clockMonotonicNs();var warm_cycles:u64=0;
    while(true) {
        for(requests[0..count]) |request| try execute(request);
        warm_cycles+=1;if(sig.os.clockMonotonicNs()-warm_start>=1000000000) break;
    }
    const warm_ns=sig.os.clockMonotonicNs()-warm_start;
    for(checks[0..check_count]) |request| try execute(request);
    try write(try sig.fmt.bufPrint(&buffer,"{{\"metadata\":true,\"engine\":\"ramsql-native\",\"rows\":{d},\"requests\":{d},\"load_ns\":{d},\"warmup_ns\":{d},\"warmup_cycles\":{d},\"backing_bytes\":{d},\"metadata_bytes\":{d},\"cell_bytes\":{d},\"work_rows\":{d},\"text_bytes\":{d},\"compact_integral_scans\":{s}}}\n",.{n,count,load_ns,warm_ns,warm_cycles,memory.bytes.len,@sizeOf(DB),@sizeOf(DB.Cell),profile.work_rows,profile.text_bytes,if(profile.compact_integral_scans) "true" else "false"}));
    for(0..5) |trial| {
        var hist:[64]u64=@splat(0);var cycles:u64=0;
        var compact_plans:u64=0;var compact_rows:u64=0;var wide_rows:u64=0;
        const start=sig.os.clockMonotonicNs();
        while(true) {
            for(requests[0..count]) |request| {
                const began=sig.os.clockMonotonicNs();try execute(request);
                const stats=db.workspace.query.stream_stats;
                compact_plans+=stats.compact_plans;compact_rows+=stats.compact_rows;wide_rows+=stats.rows-stats.compact_rows;
                const elapsed:u64=@intCast(@max(1,sig.os.clockMonotonicNs()-began));
                const bin=@min(63,64-@clz(elapsed));hist[bin]+=1;
            }
            cycles+=1;if(sig.os.clockMonotonicNs()-start>=minimum_ms*1000000) break;
        }
        const elapsed=sig.os.clockMonotonicNs()-start;
        for(checks[0..check_count]) |request| try execute(request);
        const ops=cycles*count;
        try write(try sig.fmt.bufPrint(&buffer,"{{\"trial\":{d},\"passed\":true,\"operations\":{d},\"cycles\":{d},\"elapsed_ns\":{d},\"p50_upper_ns\":{d},\"p95_upper_ns\":{d},\"p99_upper_ns\":{d},\"compact_plans\":{d},\"compact_rows\":{d},\"streamed_wide_rows\":{d}}}\n",.{trial,ops,cycles,elapsed,percentile(&hist,ops,50),percentile(&hist,ops,95),percentile(&hist,ops,99),compact_plans,compact_rows,wide_rows}));
    }
}
pub fn main(init:std.process.Init.Minimal) !void {
    run(init) catch |err| {
        var buffer:[256]u8=undefined;
        try write(try sig.fmt.bufPrint(&buffer,"{{\"passed\":false,\"error\":\"{s}\"}}\n",.{@errorName(err)}));
        return err;
    };
}
