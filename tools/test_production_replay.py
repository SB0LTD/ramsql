"""Execute every trace shape and reject corrupt input/oracle records."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import tempfile
from benchmark_production import validate
from production_workloads import CASES, write_fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    args=parser.parse_args()
    binary=args.binary.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='ramsql-production-replay-') as directory:
        folder=Path(directory)
        for case in CASES:
            trace=folder/(case+'.trace')
            fixture=write_fixture(trace,n=128,groups=17,width=96,seed=1729,case=case,count=40)
            result=subprocess.run([str(binary),str(trace),'1'],text=True,capture_output=True,timeout=60)
            assert result.returncode==0,(case,result.stdout,result.stderr)
            records=[json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
            validate(records,fixture,1)
        source=(folder/'uniform_get.trace').read_bytes()
        offset=36
        for _ in range(128):
            length=struct.unpack_from('<i',source,offset+12)[0]
            offset+=16+max(0,length)
        corrupted=bytearray(source);corrupted[offset+16]^=1
        for name,raw in [('magic',b'badmagic'+source[8:]),('short',source[:40]),('trailing',source+b'x'),('digest',corrupted)]:
            trace=folder/(name+'.trace');trace.write_bytes(raw)
            result=subprocess.run([str(binary),str(trace),'1'],text=True,capture_output=True,timeout=60)
            assert result.returncode!=0,(name,result.stdout)
            records=[json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
            assert any(record.get('passed') is False for record in records),(name,result.stdout)
    print(json.dumps(dict(suite='production-replay',passed=True,workloads=len(CASES),corrupt_inputs=4)))


if __name__=='__main__':main()
