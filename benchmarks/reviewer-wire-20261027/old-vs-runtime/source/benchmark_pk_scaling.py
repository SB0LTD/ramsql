"""Checked wire PK comparison with one client process each (no shared Python GIL).

This compares RAMSQL binaries, not native engine calls or PostgreSQL parity.
Compile ReleaseFast for like-for-like timing and retain the actual binary hashes.
"""
import argparse
import ctypes
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import socket
import time
from benchmark import process_memory
from test_server import PG, Server


def cpu_seconds(process):
    if os.name=='nt':
        fields=[ctypes.c_uint64() for _ in range(4)]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetProcessTimes.argtypes=[ctypes.c_void_p]+[ctypes.POINTER(ctypes.c_uint64)]*4
        if not kernel.GetProcessTimes(ctypes.c_void_p(int(process._handle)),*[ctypes.byref(x) for x in fields]):
            raise OSError(ctypes.get_last_error())
        return (fields[2].value+fields[3].value)/1e7
    fields=Path(f'/proc/{process.pid}/stat').read_text().rsplit(')',1)[1].split()
    return (int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK')


def client(port, identity, count, rows, start, messages):
    try:
        pg=PG(socket.create_connection(('127.0.0.1',port),timeout=15))
        pg.startup()
        queries=[(f'SELECT id,value FROM lookup WHERE id={key}',[[str(key),str(key*3)]])
                 for i in range(count) for key in [(i*48271+identity*17)%rows]]
        for query,expected in queries[:256]:pg.ok(query,expected)
        messages.put(dict(ready=identity))
        if not start.wait(60):raise TimeoutError('coordinator did not start trial')
        began=time.perf_counter_ns()
        for query,expected in queries:pg.ok(query,expected)
        elapsed=time.perf_counter_ns()-began
        pg.close()
        messages.put(dict(done=identity,operations=count,elapsed_ns=elapsed))
    except BaseException as error:
        messages.put(dict(error=type(error).__name__+': '+str(error),client=identity))
        raise


def measure(binary, clients, count, rows):
    context=mp.get_context('spawn')
    with Server(binary,max_clients=clients,extra_arguments=('--rows',str(rows))) as server:
        seed=PG(server.raw());seed.startup()
        seed.ok('CREATE TABLE lookup(id INT PRIMARY KEY,value INT)')
        for base in range(0,rows,32):
            seed.ok('INSERT INTO lookup VALUES '+','.join(f'({i},{i*3})' for i in range(base,min(rows,base+32))))
        seed.close()
        start=context.Event();messages=context.Queue()
        children=[context.Process(target=client,args=(server.port,i,count,rows,start,messages)) for i in range(clients)]
        try:
            for child in children:child.start()
            for _ in children:
                message=messages.get(timeout=60)
                assert 'ready' in message,message
            before=cpu_seconds(server.process);began=time.perf_counter_ns();start.set()
            done=[]
            for _ in children:
                message=messages.get(timeout=120)
                assert 'done' in message,message
                done.append(message)
            elapsed=time.perf_counter_ns()-began
            cpu=cpu_seconds(server.process)-before
            for child in children:
                child.join(timeout=10)
                assert child.exitcode==0,child.exitcode
            return dict(passed=True,operations=sum(x['operations'] for x in done),elapsed_ns=elapsed,
                server_cpu_seconds=cpu,server_memory=process_memory(server.process),clients=clients,
                workers=done,boundary='nonpipelined PostgreSQL simple queries; all values checked; process per client')
        finally:
            for child in children:
                if child.pid is not None and child.is_alive():child.terminate();child.join(timeout=10)
            messages.close();messages.join_thread()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',action='append',type=Path,required=True)
    parser.add_argument('--clients',type=int,nargs='+',default=[1,4,8])
    parser.add_argument('--operations',type=int,default=20000,help='operations per client per trial')
    parser.add_argument('--rows',type=int,default=8192)
    parser.add_argument('--trials',type=int,default=3)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.operations<256 or args.rows<32 or args.trials<2 or not all(1<=n<=16 for n in args.clients):parser.error('invalid workload')
    args.output.mkdir(parents=True,exist_ok=False)
    report=dict(schema=1,note='Single-host wire comparison; CPU/OS/client costs included. No PostgreSQL/scaling victory claim.',
        rows=args.rows,binaries=[dict(path=str(b.resolve()),sha256=hashlib.sha256(b.read_bytes()).hexdigest()) for b in args.binary],measurements=[])
    def save():(args.output/'results.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    save()
    for trial in range(args.trials):
        for concurrency in args.clients:
            for binary in args.binary[::(-1 if trial%2 else 1)]:
                entry=dict(binary=str(binary.resolve()),trial=trial,clients=concurrency,passed=False)
                try:entry.update(measure(binary.resolve(),concurrency,args.operations,args.rows))
                except Exception as error:entry['error']=type(error).__name__+': '+str(error)
                report['measurements'].append(entry);save()
                rate=entry.get('operations',0)*1e9/entry.get('elapsed_ns',1)
                print(binary.parent.parent.name,concurrency,trial,entry['passed'],round(rate),entry.get('error',''),flush=True)
    return 0 if all(x['passed'] for x in report['measurements']) else 1


if __name__=='__main__':raise SystemExit(main())
