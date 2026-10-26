using System.Data;
using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using irdb;

// Benchmark adapter only. RAMSQL runtime stays entirely in allocator-free Sig.
static class ProductionTrace {
    record Request(uint Kind,uint Rows,uint Columns,ulong Digest,string Sql);
    static ulong Mix(ulong h,ulong v)=>unchecked(h*1099511628211UL)^v;
    static Request Read(BinaryReader r) {
        uint kind=r.ReadUInt32(),size=r.ReadUInt32(),rows=r.ReadUInt32(),cols=r.ReadUInt32();ulong digest=r.ReadUInt64();
        if((kind!=1 && kind!=2)||size==0||size>65536||rows>2048||cols>4) throw new Exception("InvalidTrace");
        byte[] raw=r.ReadBytes((int)size);if(raw.Length!=size) throw new EndOfStreamException();
        return new(kind,rows,cols,digest,new UTF8Encoding(false,true).GetString(raw));
    }
    public static void Run(string[] args) {
        try { Execute(args); }
        catch(Exception e) {Console.WriteLine();Console.WriteLine(JsonSerializer.Serialize(new{passed=false,error=e.GetType().Name+": "+e.Message}));Environment.ExitCode=1;}
    }
    static void Execute(string[] args) {
        int minimumMs=args.Length==2?int.Parse(args[1]):200;
        if(args.Length<1||args.Length>2||minimumMs<1||minimumMs>10000) throw new ArgumentException();
        var file=new FileInfo(args[0]);if(file.Length<36||file.Length>1024L*1024*1024) throw new Exception("InvalidTrace");
        using var r=new BinaryReader(new MemoryStream(File.ReadAllBytes(args[0])),Encoding.UTF8);
        if(Encoding.ASCII.GetString(r.ReadBytes(8))!="RAMTRC01") throw new Exception("InvalidTrace");
        uint n=r.ReadUInt32(),groups=r.ReadUInt32(),width=r.ReadUInt32(),count=r.ReadUInt32(),checkCount=r.ReadUInt32();_=r.ReadUInt64();
        if(n<64||n>10000000||groups<1||groups>2048||width<1||width>512||count<1||count>65536||checkCount<1||checkCount>16) throw new Exception("InvalidTrace");
        var data=new DataTable();
        data.Columns.Add("id",typeof(int));data.Columns.Add("value",typeof(int));data.Columns.Add("category",typeof(int));data.Columns.Add("payload",typeof(string));
        data.PrimaryKey=new[]{data.Columns["id"]!};
        var load=Stopwatch.StartNew();
        for(int i=0;i<n;i++) {
            int id=r.ReadInt32(),value=r.ReadInt32(),category=r.ReadInt32(),length=r.ReadInt32();
            if(length < -1 || length>width) throw new Exception("InvalidTrace");
            object text=DBNull.Value;
            if(length>=0) {byte[] raw=r.ReadBytes(length);if(raw.Length!=length) throw new EndOfStreamException();text=new UTF8Encoding(false,true).GetString(raw);}
            data.Rows.Add(id,value,category,text);
        }
        var db=new InMemoryDatabase();var imported=new InMemoryTable("bench",data,true);db.add("bench",imported);
        imported.createClusteredIndex("id");_=imported.getClusteredIndex(db);load.Stop();
        if(!imported.isCompiled||!imported.hasClusteredIndex()) throw new Exception("IRDB import/index not enabled");
        Request[] requests=Enumerable.Range(0,(int)count).Select(_=>Read(r)).ToArray();
        Request[] checks=Enumerable.Range(0,(int)checkCount).Select(_=>Read(r)).ToArray();
        if(r.BaseStream.Position!=r.BaseStream.Length) throw new Exception("TrailingTraceBytes");
        void Query(Request q) {
            var table=db.execute(q.Sql,CancellationToken.None)??throw new Exception("NullResult");
            if(q.Kind==2) {if(table.rowCount!=q.Rows) throw new Exception("WrongAffectedCount");return;}
            if(table.rowCount!=q.Rows||table.getColumnCount()!=q.Columns) throw new Exception("WrongShape: "+q.Sql);
            ulong h=Mix((ulong)table.rowCount,(ulong)table.getColumnCount());
            for(int row=0;row<table.rowCount;row++) for(int col=0;col<table.getColumnCount();col++) {
                var column=table.getColumn(col);
                // Use the public typed/null-aware accessors. Boxing every
                // integer would inflate the reference engine's GC costs.
                if(column is InMemoryColumn<int> ints) {
                    int number=ints.getValueWithNull(row,out bool missing);
                    h=missing?Mix(h,0):Mix(Mix(h,1),unchecked((ulong)number));continue;
                }
                if(column is InMemoryColumn<long> longs) {
                    long number=longs.getValueWithNull(row,out bool missing);
                    h=missing?Mix(h,0):Mix(Mix(h,1),unchecked((ulong)number));continue;
                }
                object? value=column.getValueObj(row);
                if(value==null||value is DBNull) h=Mix(h,0);
                else if(value is string text) {
                    // Fixture payloads are ASCII; no per-result UTF8 allocation.
                    h=Mix(Mix(h,2),(ulong)text.Length);
                    foreach(char c in text) {if(c>127) throw new Exception("NonAsciiResult");h=Mix(h,c);}
                } else h=Mix(Mix(h,1),unchecked((ulong)Convert.ToInt64(value,CultureInfo.InvariantCulture)));
            }
            if(h!=q.Digest) throw new Exception("WrongDigest: "+q.Sql);
        }
        foreach(var q in checks) Query(q);
        var warm=Stopwatch.StartNew();ulong warmCycles=0;
        do {foreach(var q in requests) Query(q);warmCycles++;} while(warm.ElapsedMilliseconds<1000);
        warm.Stop();
        foreach(var q in checks) Query(q);
        Console.WriteLine(); // IRDB emits progress dots without a newline during import.
        Console.WriteLine(JsonSerializer.Serialize(new{metadata=true,engine="InMemory.Net.irdb",package_version="1.10.821",runtime=System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription,rows=n,requests=count,load_ns=load.Elapsed.TotalNanoseconds,warmup_ns=warm.Elapsed.TotalNanoseconds,warmup_cycles=warmCycles,import_compiled=imported.isCompiled,id_clustered_index=imported.hasClusteredIndex()}));
        static ulong Percentile(ulong[] histogram,ulong total,ulong percent) {
            ulong target=(total*percent+99)/100,sum=0;
            for(int bin=0;bin<64;bin++) {sum+=histogram[bin];if(sum>=target) return 1UL<<bin;}
            return 0;
        }
        for(int trial=0;trial<5;trial++) {
            ulong[] hist=new ulong[64];ulong cycles=0;long start=Stopwatch.GetTimestamp();
            while(true) {
                foreach(var q in requests) {
                    long began=Stopwatch.GetTimestamp();Query(q);
                    ulong ns=(ulong)Math.Max(1,(Stopwatch.GetTimestamp()-began)*1e9/Stopwatch.Frequency);
                    int bin=Math.Min(63,64-System.Numerics.BitOperations.LeadingZeroCount(ns));hist[bin]++;
                }
                cycles++;if((Stopwatch.GetTimestamp()-start)*1000.0/Stopwatch.Frequency>=minimumMs) break;
            }
            double elapsed=(Stopwatch.GetTimestamp()-start)*1e9/Stopwatch.Frequency;
            foreach(var q in checks) Query(q);
            ulong operations=cycles*count;
            Console.WriteLine(JsonSerializer.Serialize(new{trial,passed=true,operations,cycles,elapsed_ns=elapsed,p50_upper_ns=Percentile(hist,operations,50),p95_upper_ns=Percentile(hist,operations,95),p99_upper_ns=Percentile(hist,operations,99)}));
        }
    }
}
