"""Reproducible IRDB/RAMSQL native API and PostgreSQL/RAMSQL wire comparisons.

All measured requests consume and validate their results. Native-API and wire
measurements are different boundaries and are never ranked in the same table.
Only isolated, test-owned PostgreSQL clusters and RAMSQL processes are used.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import tempfile

from benchmark import cpu_name
from test_postgres_compat import native_postgres, running_ramsql

CASES = ("primary_key_hot", "filtered_limit", "sort_limit", "aggregate", "group_aggregate", "mixed")
MASK = (1 << 64) - 1
ROOT = Path(__file__).resolve().parents[1]


def digest(rows):
    result = len(rows)
    for row in rows:
        for value in row:
            result = ((result * 1099511628211) ^ (int(value) & MASK)) & MASK
    return result


def queries(n):
    return (f"SELECT id, value FROM bench WHERE id = {n//2}",
            "SELECT id, value FROM bench WHERE category = 7 ORDER BY id LIMIT 16",
            "SELECT id, value FROM bench ORDER BY value, id LIMIT 16",
            "SELECT COUNT(*), SUM(value) FROM bench",
            "SELECT category, COUNT(*), SUM(value) FROM bench GROUP BY category ORDER BY category",
            f"UPDATE bench SET value = 7 WHERE id = {n//2}")


def expected(n, changed=False):
    value = lambda i: 7 if changed and i == n//2 else n-i
    top = list(range(n-16,n))[::-1]
    if changed:
        # An independent model, not RAMSQL's heap selection implementation.
        top = sorted([*range(n-16,n), n//2], key=lambda i:(value(i),i))[:16]
    groups = []
    for g in range(64):
        count = (n-1-g)//64+1
        groups.append((g,count,count*n-g*count-64*count*(count-1)//2))
    answers = {
        "primary_key_hot": [(n//2,value(n//2))],
        "filtered_limit": [(i,value(i)) for i in range(7,n,64)][:16],
        "sort_limit": [(i,value(i)) for i in top],
        "aggregate": [(n,n*(n+1)//2)],
        "group_aggregate": groups,
    }
    result = {name:digest(rows) for name,rows in answers.items()}
    result["mixed"] = (result["primary_key_hot"]+result["filtered_limit"]+result["sort_limit"]+1)&MASK
    return result


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_process(command, log, timeout=1200):
    began = time.monotonic()
    with log.open("wb") as out:
        result = subprocess.run([str(x) for x in command],stdout=out,stderr=subprocess.STDOUT,timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"process failed ({result.returncode}); see {log}")
    return log.read_text(encoding="utf-8"),time.monotonic()-began


def validate_samples(name, samples, n, iterations):
    changed=name=="mixed"
    wanted=expected(n,changed)[name]
    assert len(samples)==5 and {s["trial"] for s in samples}==set(range(5)),"missing/duplicate trials"
    for sample in samples:
        assert sample["operations"]==iterations*(4 if changed else 1),"wrong operation count"
        assert sample["elapsed_ns"]>0,"invalid clock interval"
        assert sample["checksum"]==(wanted*iterations)&MASK,"result checksum differs from independent model"
    return wanted


def validate_stream_samples(name, samples, n):
    """Check optional executor evidence; older baseline binaries omit it."""
    fields=("stream_rows","stream_groups","stream_probes","group_cells","group_refs")
    if not any(field in sample for sample in samples for field in fields):
        return
    for sample in samples:
        assert all(type(sample.get(field)) is int and sample[field]>=0 for field in fields),"incomplete/invalid stream evidence"
        if name in ("aggregate","group_aggregate"):
            assert sample["stream_rows"]==n,"stream did not examine the modeled input"
            assert sample["stream_groups"]==(1 if name=="aggregate" else 64),"wrong streamed group count"
            assert sample["group_cells"]>0,"missing aggregate state evidence"
            if name=="aggregate":
                assert sample["stream_probes"]==sample["group_refs"]==0,"scalar aggregate materialized hash state"
            else:
                assert sample["stream_probes"]>=n and sample["group_refs"]>0,"missing grouped hash evidence"
        else:
            assert all(sample[field]==0 for field in fields),"stale stream counters on a different workload"
    for field in fields:
        assert len({sample[field] for sample in samples})==1,"stream evidence changed between identical trials"


def native_measure(binary,n,iterations,log):
    raw,wall=run_process([binary,n,iterations],log)
    records=[json.loads(line) for line in raw.splitlines() if line.startswith("{")]
    metadata=[x for x in records if "case" not in x]
    assert len(metadata)==1,"missing native provenance"
    measurements=[]
    for name in CASES:
        samples=[x for x in records if x.get("case")==name]
        assert samples,"missing native case"
        if any(x.get("passed") is False for x in samples):
            assert len(samples)==1,"partial native timing was reported as a case failure"
            measurements.append({"name":name,"passed":False,"error":samples[0]["error"]})
        else:
            wanted=validate_samples(name,samples,n,iterations)
            validate_stream_samples(name,samples,n)
            assert all(x["expected_digest"]==wanted for x in samples),"native model differs"
            measurements.append({"name":name,"passed":True,"samples":samples})
    assert {r.get("case") for r in records if "case" in r}==set(CASES),"unknown native case"
    return {"engine":"ramsql-native","boundary":"in-process public API","durability":"volatile memory",**metadata[0],"wall_seconds":wall,"measurements":measurements}


def irdb_measure(dotnet,dll,n,iterations,log):
    raw,wall=run_process([dotnet,dll,n,iterations],log)
    records=[json.loads(line) for line in raw.splitlines() if line.startswith("{")]
    assert len(records)==1,"missing IRDB report"
    result=records[0]
    assert result["package_version"]=="1.10.821" and result["import_compiled"] is True and result["id_clustered_index"] is True,"wrong IRDB version/import mode"
    assert len(result["measurements"])==len(CASES) and {x["name"] for x in result["measurements"]}==set(CASES)
    for case in result["measurements"]:
        if case["passed"]:
            wanted=validate_samples(case["name"],case["samples"],n,iterations)
            assert case["expected_digest"]==wanted,"IRDB model differs"
    result["wall_seconds"]=wall
    return result


def wire_measure(psycopg,arguments,n,iterations,engine,unlogged=False):
    with psycopg.connect(**arguments) as connection:
        connection.execute("SET statement_timeout = 30000",prepare=False)
        if unlogged: connection.execute("SET synchronous_commit = off",prepare=False)
        connection.execute("CREATE "+("UNLOGGED " if unlogged else "")+"TABLE bench (id INT PRIMARY KEY, value INT, category INT, label TEXT)",prepare=False)
        load=time.monotonic()
        for start in range(0,n,32):
            values=",".join(f"({i},{n-i},{i%64},'value')" for i in range(start,min(n,start+32)))
            cursor=connection.execute("INSERT INTO bench VALUES "+values,prepare=False)
            assert cursor.rowcount==min(32,n-start),"seed row loss"
        assert connection.execute("SELECT COUNT(*) FROM bench",prepare=False).fetchone()==(n,),"seed count"
        loaded=time.monotonic()-load
        sql=queries(n)
        measurements=[]
        for name in CASES:
            try:
                changed=name=="mixed"
                wanted=expected(n,changed)[name]
                def request(index):
                    cursor=connection.execute(sql[index],prepare=False)
                    if index==5:
                        assert cursor.rowcount==1,"wrong update count"
                        return 1
                    return digest(cursor.fetchall())
                if changed: request(5)
                def one():
                    return sum(request(i) for i in (0,1,2,5))&MASK if changed else request(CASES.index(name))
                for _ in range(10): assert one()==wanted,"warmup differs from model"
                samples=[]
                for trial in range(5):
                    checksum=0
                    began=time.perf_counter_ns()
                    for _ in range(iterations):
                        answer=one()
                        assert answer==wanted,"timed answer differs from model"
                        checksum=(checksum+answer)&MASK
                    elapsed=time.perf_counter_ns()-began
                    samples.append({"trial":trial,"operations":iterations*(4 if changed else 1),"elapsed_ns":elapsed,"checksum":checksum})
                validate_samples(name,samples,n,iterations)
                measurements.append({"name":name,"passed":True,"samples":samples})
            except Exception as error:
                measurements.append({"name":name,"passed":False,"error":type(error).__name__+": "+str(error)[:1000],"sqlstate":getattr(error,"sqlstate",None)})
        return {"engine":engine,"rows":n,"boundary":"loopback PostgreSQL simple query via psycopg; fetch and digest included","durability":"unlogged table, synchronous_commit=off" if unlogged else "RAMSQL --memory; no durable commit flush","load_seconds":loaded,"measurements":measurements}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ramsql",type=Path,required=True)
    parser.add_argument("--native",type=Path,required=True)
    parser.add_argument("--irdb",type=Path,required=True)
    parser.add_argument("--dotnet",type=Path,required=True)
    parser.add_argument("--postgres-bin",type=Path,required=True)
    parser.add_argument("--postgres-work-directory",type=Path,default=Path(tempfile.gettempdir())/"ramsql-review-postgres",help="native filesystem for owned PostgreSQL clusters")
    parser.add_argument("--zpm-source",type=Path,required=True,help="actual source used to build the candidate")
    parser.add_argument("--rows",type=int,nargs="+",default=[512,100000,1000000])
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    import psycopg
    assert all(64<=n<=10000000 for n in args.rows)
    folder=args.output.resolve();folder.mkdir(parents=True,exist_ok=False)
    files=[ROOT/"src/review_benchmark.sig",ROOT/"src/server.sig",Path(__file__),ROOT/"tools/irdb_bench/Program.cs",ROOT/"tools/irdb_bench/packages.lock.json"]
    files+=list((args.zpm_source/"src/core/sql").glob("*.sig"))+[args.zpm_source/"src/core/sql.sig",args.zpm_source/"src/platform/page_region.sig"]
    report={"schema":1,"started_utc":datetime.now(timezone.utc).isoformat(),"environment":{"os":platform.platform(),"cpu":cpu_name(),"logical_cpus":os.cpu_count(),"python":platform.python_version(),"psycopg":psycopg.__version__},"binaries":{name:{"path":str(path.resolve()),"sha256":sha(path)} for name,path in [("ramsql",args.ramsql),("native",args.native),("irdb_adapter",args.irdb),("irdb_library",args.irdb.parent/"irdb.dll")]},"source_sha256":{str(p.resolve()):sha(p) for p in files},"irdb_package":{"id":"InMemory.Net.irdb","version":"1.10.821","source":"https://api.nuget.org/v3-flatcontainer/inmemory.net.irdb/1.10.821/inmemory.net.irdb.1.10.821.nupkg","verified_download_sha256":"9c3b517efdb9a56e46c2c41aa3ea2da10cd67700b20cc845197554f2efb2f90b"},"scope":["Fixed hot-key and deterministic SQL-subset workloads; this does not reproduce the unavailable reviewer scripts.","Native API and server/client boundaries are separate comparisons. No cross-boundary winner is claimed.","Single client, volatile memory tier. PostgreSQL uses an unlogged table and synchronous_commit=off. Durable benchmarks remain a separate experiment.","Failed or capacity-blocked cases remain visible and have no throughput score.","Five trials, ten untimed warmups per case; result values consumed and checked against an independent model.","No performance threshold determines correctness. These results do not establish production readiness or comprehensive PostgreSQL parity."],"datasets":[]}
    try:
        # Runs are sequential: no competing engine benchmark or build is launched here.
        for n in args.rows:
            iterations=2000 if n<=512 else 100 if n<=100000 else 10
            dataset={"rows":n,"iterations_per_trial":iterations,"sql":dict(zip(CASES,queries(n)[:5]+("four SQL requests: point, filter, sort, update",))),"engines":[]}
            report["datasets"].append(dataset)
            dataset["engines"].append(native_measure(args.native,n,iterations,folder/f"native-{n}.jsonl"))
            dataset["engines"].append(irdb_measure(args.dotnet,args.irdb,n,iterations,folder/f"irdb-{n}.jsonl"))
            with native_postgres("unused",psycopg,binary_directory=args.postgres_bin,work_directory=args.postgres_work_directory/f"pg-{n}") as (connection,metadata):
                dataset["postgres_reference"]=metadata
                dataset["engines"].append(wire_measure(psycopg,connection,n,iterations,"postgres-wire",True))
            try:
                with running_ramsql(args.ramsql,extra_arguments=("--rows",str(n))) as connection:
                    dataset["engines"].append(wire_measure(psycopg,connection,n,iterations,"ramsql-wire"))
            except Exception as error:
                failure=type(error).__name__+": "+str(error)[:1000]
                dataset["engines"].append({"engine":"ramsql-wire","rows":n,"setup_failed":True,"error":failure,"measurements":[{"name":name,"passed":False,"error":"setup failed: "+failure} for name in CASES]})
            print(f"completed {n} rows",flush=True)
    finally:
        report["finished_utc"]=datetime.now(timezone.utc).isoformat()
        report["all_workloads_supported_and_correct"]=len(report["datasets"])==len(args.rows) and all(len(d["engines"])==4 and all(c["passed"] for e in d["engines"] for c in e["measurements"]) for d in report["datasets"])
        (folder/"results.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        lines=["# IRDB / RAMSQL / PostgreSQL benchmark", "", "Native API and server/client costs are separate tiers. All values are median operations/second over five trials.", ""]
        for dataset in report["datasets"]:
            lines += [f"## {dataset['rows']:,} rows", "", "| Workload | RAMSQL native API | IRDB native API | RAMSQL wire | PostgreSQL wire |", "|---|---:|---:|---:|---:|"]
            for name in CASES:
                cells=[]
                for engine in ("ramsql-native","InMemory.Net.irdb","ramsql-wire","postgres-wire"):
                    matches=[e for e in dataset["engines"] if e["engine"]==engine]
                    case=next((x for x in matches[0]["measurements"] if x["name"]==name),None) if matches else None
                    cells.append("not completed" if case is None else "FAILED: "+case.get("error","unknown").replace("|","/").splitlines()[0][:90] if not case["passed"] else f"{statistics.median(s['operations']*1e9/s['elapsed_ns'] for s in case['samples']):,.0f}")
                lines.append("| "+name+" | "+" | ".join(cells)+" |")
            lines.append("")
        lines += ["## Scope and reproducibility", ""]+["- "+x for x in report["scope"]]
        (folder/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return 0

if __name__=="__main__": raise SystemExit(main())
