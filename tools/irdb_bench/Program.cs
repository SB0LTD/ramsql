using System.Data;
using System.Diagnostics;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.Json;
using irdb;

if(args.Length>0 && args[0]=="--column-api") {
 foreach(var m in typeof(InMemoryColumn<int>).GetMethods(BindingFlags.Public|BindingFlags.Instance).Where(m=>m.Name.Contains("null",StringComparison.OrdinalIgnoreCase)||m.Name.Contains("Value"))) Console.WriteLine(m.ToString());
 return;
}
if(args.Length>0 && args[0]=="--trace") { ProductionTrace.Run(args.Skip(1).ToArray());return; }
int n = args.Length > 0 ? int.Parse(args[0]) : 512;
int iterations = args.Length > 1 ? int.Parse(args[1]) : n <= 512 ? 2000 : n <= 100000 ? 100 : 10;
int minimumMs = args.Length == 4 && args[2] == "--minimum-ms" ? int.Parse(args[3]) : 0;
if(args.Length > 2 && (args.Length != 4 || args[2] != "--minimum-ms" || minimumMs < 1)) throw new ArgumentException("invalid calibration arguments");
const int trials = 5;
if(n < 64 || n > 10000000 || iterations < 1) throw new ArgumentException("invalid workload");
var load = Stopwatch.StartNew();
var db = new InMemoryDatabase();
var data = new DataTable();
data.Columns.Add("id",typeof(int)); data.Columns.Add("value",typeof(int));
data.Columns.Add("category",typeof(int)); data.Columns.Add("label",typeof(string));
data.PrimaryKey = new[]{data.Columns["id"]!};
for(int i=0;i<n;i++) data.Rows.Add(i,n-i,i%64,"value");
var imported = new InMemoryTable("bench",data,true);
db.add("bench",imported);
imported.createClusteredIndex("id");
_ = imported.getClusteredIndex(db);
bool initialCompiled=imported.isCompiled;
bool initialIndexed=imported.hasClusteredIndex();
Console.Error.WriteLine("import parameter: " + typeof(InMemoryTable).GetConstructor(new[]{typeof(string),typeof(DataTable),typeof(bool)})!.GetParameters()[2].Name + "; compiled=" + imported.isCompiled);
load.Stop();
var cases = new [] {
 ("primary_key_hot",$"SELECT id, value FROM bench WHERE id = {n/2}"),
 ("filtered_limit","SELECT id, value FROM bench WHERE category = 7 ORDER BY id LIMIT 16"),
 ("sort_limit","SELECT id, value FROM bench ORDER BY value, id LIMIT 16"),
 ("aggregate","SELECT COUNT(*), SUM(value) FROM bench"),
 ("group_aggregate","SELECT category, COUNT(*), SUM(value) FROM bench GROUP BY category ORDER BY category"),
 ("mixed","")
};
ulong Digest(IEnumerable<long[]> rows) {
 var list=rows.ToArray(); ulong h=(ulong)list.Length;
 foreach(var row in list) foreach(long value in row) h=unchecked(h*1099511628211UL)^(ulong)value;
 return h;
}
long Value(int id,bool changed) => changed && id==n/2 ? 7 : n-id;
ulong Expected(string name,bool changed) {
 IEnumerable<long[]> rows = name switch {
  "primary_key_hot" => new [] {new long[]{n/2,Value(n/2,changed)}},
  "filtered_limit" => Enumerable.Range(0,n).Where(i=>i%64==7).Take(16).Select(i=>new long[]{i,Value(i,changed)}),
  "sort_limit" => Enumerable.Range(0,n).OrderBy(i=>Value(i,changed)).ThenBy(i=>i).Take(16).Select(i=>new long[]{i,Value(i,changed)}),
  "aggregate" => new [] {new long[]{n,(long)n*(n+1)/2}},
  "group_aggregate" => Enumerable.Range(0,64).Select(g=>new long[]{g,(n-1-g)/64+1,Enumerable.Range(0,n).Where(i=>i%64==g).Sum(i=>(long)(n-i))}),
  _=>throw new Exception("unknown model")
 };
 return Digest(rows);
}
ulong Query(string sql,bool write=false) {
 var table=db.execute(sql,CancellationToken.None);
 if(table==null) throw new Exception("IRDB returned a null result");
 if(write) {if(table.rowCount!=1) throw new Exception("wrong update count");return 1;}
 ulong h=(ulong)table.rowCount;
 for(int row=0;row<table.rowCount;row++) for(int col=0;col<table.getColumnCount();col++) {
  var column=table.getColumn(col);
  long value=column switch {InMemoryColumn<int> x=>x.getValue(row),InMemoryColumn<long> x=>x.getValue(row),_=>Convert.ToInt64(column.getValueObj(row))};
  h=unchecked(h*1099511628211UL)^(ulong)value;
 }
 return h;
}
var measurements=new List<object>();
foreach(var (name,sql) in cases) {
 try {
  bool mixed=name=="mixed";
  string update=$"UPDATE bench SET value = 7 WHERE id = {n/2}";
  if(mixed) Query(update,true);
  ulong expected=mixed ? unchecked(Expected("primary_key_hot",true)+Expected("filtered_limit",true)+Expected("sort_limit",true)+1) : Expected(name,false);
  ulong Run() => mixed ? unchecked(Query(cases[0].Item2)+Query(cases[1].Item2)+Query(cases[2].Item2)+Query(update,true)) : Query(sql);
  for(int warm=0;warm<10;warm++) if(Run()!=expected) throw new Exception("warmup result mismatch");
  int count=iterations;
  var calibration=new List<object>();
  if(minimumMs>0) {
   bool converged=false;
   for(int attempt=0;attempt<8;attempt++) {
    long began=Stopwatch.GetTimestamp();
    for(int i=0;i<count;i++) if(Run()!=expected) throw new Exception("calibration result mismatch");
    double elapsedNs=(Stopwatch.GetTimestamp()-began)*1e9/Stopwatch.Frequency;
    calibration.Add(new {iterations=count,elapsed_ns=elapsedNs});
    if(elapsedNs>=minimumMs*1e6) {converged=true;break;}
    count=Math.Max(count+1,checked((int)Math.Ceiling(count*minimumMs*1e6/Math.Max(elapsedNs,1)*1.25)));
    if(count>10000000) throw new Exception("calibration iteration limit");
   }
   if(!converged) throw new Exception("calibration did not converge");
  }
  var samples=new List<object>();
  for(int trial=0;trial<trials;trial++) {
   ulong checksum=0;
   long start=Stopwatch.GetTimestamp();
   for(int i=0;i<count;i++) {ulong answer=Run();if(answer!=expected) throw new Exception("timed result mismatch");checksum=unchecked(checksum+answer);}
   long elapsed=Stopwatch.GetTimestamp()-start;
   long operations=(long)count*(mixed?4:1);
   samples.Add(new {trial,operations,elapsed_ns=(double)elapsed*1e9/Stopwatch.Frequency,ops_per_second=operations*(double)Stopwatch.Frequency/elapsed,checksum});
  }
  measurements.Add(new{name,sql=mixed?new[]{cases[0].Item2,cases[1].Item2,cases[2].Item2,update}:new[]{sql},passed=true,expected_digest=expected,iterations=count,calibration,samples});
 } catch(Exception e) {measurements.Add(new{name,passed=false,error=e.GetType().Name+": "+e.Message});}
}
Console.WriteLine();
Console.WriteLine(JsonSerializer.Serialize(new {schema=1,engine="InMemory.Net.irdb",package_version="1.10.821",assembly=typeof(InMemoryDatabase).Assembly.GetName().Version?.ToString(),runtime=RuntimeInformation.FrameworkDescription,os=RuntimeInformation.OSDescription,boundary="in-process public API; result values consumed; no DataTable conversion in timing",durability="volatile memory; no per-commit durable flush",import_compiled=initialCompiled,id_clustered_index=initialIndexed,id_index_after_workload=imported.hasClusteredIndex(),rows=n,load_ms=load.Elapsed.TotalMilliseconds,working_set=Process.GetCurrentProcess().WorkingSet64,measurements}));
