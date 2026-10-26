"""Check generated SQL/results against an owned independent PostgreSQL cluster."""
import argparse
import json
from pathlib import Path
import tempfile
from production_workloads import CASES, digest, read_fixture, write_fixture
from test_postgres_compat import native_postgres


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--postgres-bin',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    import psycopg
    folder=args.output.resolve();folder.mkdir(parents=True,exist_ok=False)
    checks=0
    with native_postgres('unused',psycopg,binary_directory=args.postgres_bin,
                         work_directory=Path(tempfile.mkdtemp(prefix='ramsql-production-model-'))) as (connection,reference):
        with psycopg.connect(**connection) as db:
            for case in CASES:
                trace=folder/(case+'.trace')
                write_fixture(trace,n=128,groups=17,width=96,seed=20261026,case=case,count=160)
                rows,requests,post=read_fixture(trace)
                db.execute('CREATE TABLE bench (id INT PRIMARY KEY,value INT,category INT,payload TEXT)')
                with db.cursor() as cursor:cursor.executemany('INSERT INTO bench VALUES (%s,%s,%s,%s)',rows)
                for _ in range(2):
                    for kind,count,columns,wanted,sql in [*requests,*post]:
                        cursor=db.execute(sql,prepare=False)
                        if kind==2:assert cursor.rowcount==count,(case,sql)
                        else:
                            values=cursor.fetchall()
                            assert len(values)==count and len(cursor.description)==columns,(case,sql)
                            assert digest(values,columns)==wanted,(case,sql,values)
                        checks+=1
                db.execute('DROP TABLE bench')
                print(case,'checked twice',flush=True)
    report=dict(passed=True,cases=len(CASES),cycles=2,checks=checks,reference=reference)
    (folder/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
