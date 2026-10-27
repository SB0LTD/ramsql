"""Independent CLI and PG admission checks for machine-scoped memory grants."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from test_server import PG, Server, errors, startup_packet, expect_closed

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=ROOT/'sig-out/bin'/('ramsql.exe' if os.name=='nt' else 'ramsql'))
    args=parser.parse_args();binary=args.binary.resolve(strict=True)
    def report(*arguments):
        result=subprocess.run([str(binary),'--machine-capacity',*arguments],capture_output=True,timeout=15)
        assert result.returncode==0 and not result.stderr,result
        return json.loads(result.stdout)
    native=report()
    assert native['scope']=='machine_capacity' and native['usable_cpus']>0
    assert 0<=native['available_physical']<=native['physical_total']
    assert native['process_budget_bytes']<=native['physical_total']
    assert native['preferred_storage_bytes']+native['preferred_execution_bytes']==native['shared_pool_bytes']
    assert native['server_region_budgeting'] and not native['elastic_sql_storage'] and not native['parallel_sql']
    capped=report('--memory-budget',str(256*1024**2),'--os-headroom',str(32*1024**2))
    assert capped['process_budget_bytes']<=256*1024**2 and capped['headroom_bytes']==32*1024**2
    with tempfile.TemporaryDirectory(prefix='ramsql-machine-capacity-') as directory:
        wal=Path(directory)/'must-not-exist.wal'
        report('--data',str(wal),'--rows','100000000')
        assert not wal.exists()
        failed=subprocess.run([str(binary),'--serve','--insecure-local','--data',str(wal),
                               '--memory-budget','1','--port','0'],capture_output=True,timeout=15)
        assert failed.returncode==1 and not wal.exists() and b'BudgetExceeded' in failed.stderr,failed
    for bad in (['--memory-budget','0'],['--memory-budget','-1'],['--memory-budget','18446744073709551616']):
        result=subprocess.run([str(binary),'--machine-capacity',*bad],capture_output=True,timeout=15)
        assert result.returncode==2,(bad,result)
    # The deliberately small byte budget admits fewer than the declared eight
    # slots. Exhaust real buffers, retain existing connections and release/reuse.
    for workers in (1,4):
        if workers>1:
            caps=json.loads(subprocess.check_output([str(binary),'--capabilities']))
            if not caps['native_workers_available']:
                continue
        with Server(binary,max_clients=8,extra_arguments=('--memory-budget',str(256*1024**2),
                    '--os-headroom',str(32*1024**2),'--workers',str(workers))) as server:
            held=[]
            try:
                rejection=None
                for _ in range(8):
                    client=PG(server.raw());client.sock.sendall(startup_packet())
                    messages=[]
                    while True:
                        message=client.message();messages.append(message)
                        if message[0] in (b'E',b'Z'):
                            break
                    failure=errors(messages)
                    if failure:
                        assert failure[0]['C']=='53200' and failure[0]['S']=='FATAL',failure
                        expect_closed(client);client.close(terminate=False);rejection=failure;break
                    assert messages[-1][0]==b'Z',messages
                    held.append(client)
                assert held and rejection is not None,(workers,len(held),rejection)
                for client in held:
                    client.ok('SELECT 1',[['1']])
                released=held.pop();released.close()
                time.sleep(0.1)
                replacement=PG(server.raw())
                try:
                    replacement.startup();replacement.ok('SELECT 1',[['1']])
                finally:
                    replacement.close()
            finally:
                for client in held:
                    client.close()
    print('PASS machine capacity: native facts, checked plans, preflight, startup and real PG memory admission/reuse')

if __name__=='__main__':
    main()
