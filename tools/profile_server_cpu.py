"""Sample an owned server's user CPU; keep profiler timings out of speed claims."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import select
import signal
import subprocess
import tempfile
import time

from test_server import Server
from test_persistence import DurableServer
from profile_reports import compact_report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--perf',type=Path,required=True)
    parser.add_argument('--mode',choices=('read','durable-update'),required=True)
    parser.add_argument('--iterations',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.iterations<1: parser.error('iterations must be positive')
    import psycopg
    binary=args.binary.resolve(strict=True);folder=args.output.resolve()
    folder.mkdir(parents=True,exist_ok=False)
    durable=args.mode=='durable-update';wal=folder/'database.wal'
    controls=Path(tempfile.mkdtemp(prefix='ramsql-server-perf-'))
    control=controls/'control';ack=controls/'ack'
    os.mkfifo(control);os.mkfifo(ack)
    ctl=os.open(control,os.O_RDWR|os.O_NONBLOCK);reader=os.open(ack,os.O_RDWR|os.O_NONBLOCK)
    recorder=None
    def command(value):
        os.write(ctl,(value+'\n').encode())
        if not select.select([reader],[],[],20)[0] or b'ack' not in os.read(reader,1024):
            raise RuntimeError('perf did not acknowledge '+value)
    def cpu_ticks(pid):
        fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return int(fields[11])+int(fields[12])
    def connect(server):
        return psycopg.connect(host='127.0.0.1',port=server.port,user='ramsql',dbname='ramsql',
                               autocommit=True,sslmode='disable',connect_timeout=3)
    started=time.time()
    try:
        manager=DurableServer(binary,wal,create=True) if durable else Server(binary)
        with manager as server,connect(server) as db:
            db.execute('CREATE TABLE bench (id INT PRIMARY KEY, value INT)')
            values=','.join(f'({i},{64-i})' for i in range(64))
            db.execute('INSERT INTO bench VALUES '+values)
            with (folder/'perf.log').open('w') as log:
                recorder=subprocess.Popen([str(args.perf.resolve()),'record','-e','cpu-clock:u','-F','499',
                    '--call-graph','dwarf,8192','--delay=-1',f'--control=fifo:{control},{ack}',
                    '-p',str(server.process.pid),'-o',str(folder/'perf.data')],stdout=log,stderr=subprocess.STDOUT)
                command('enable')
                ticks=cpu_ticks(server.process.pid);began=time.perf_counter_ns()
                for _ in range(args.iterations):
                    if durable:
                        assert db.execute('UPDATE bench SET value = value + 1 WHERE id = 31',prepare=False).rowcount==1
                    else:
                        assert db.execute('SELECT id, value FROM bench WHERE id = 31',prepare=False).fetchall()==[(31,33)]
                elapsed=time.perf_counter_ns()-began
                cpu=(cpu_ticks(server.process.pid)-ticks)/os.sysconf('SC_CLK_TCK')
                command('disable');recorder.send_signal(signal.SIGINT)
                status=recorder.wait(timeout=20)
                if status not in (0,-signal.SIGINT): raise RuntimeError('perf failed')
            wanted=33+(args.iterations if durable else 0)
            assert db.execute('SELECT value FROM bench WHERE id = 31',prepare=False).fetchone()==(wanted,)
        if durable:
            # DurableServer stops its owned process abruptly. Reopen must see
            # every acknowledged update; this checks process crash recovery.
            with DurableServer(binary,wal) as server,connect(server) as db:
                assert db.execute('SELECT value FROM bench WHERE id = 31',prepare=False).fetchone()==(wanted,)
        report=subprocess.run([str(args.perf.resolve()),'report','--stdio','--no-children','-g','none',
            '--percent-limit','0.5','--sort','symbol','-i',str(folder/'perf.data')],capture_output=True,text=True,check=True).stdout
        (folder/'self-full.txt.gz').write_bytes(gzip.compress(report.encode(),mtime=0))
        (folder/'self.txt').write_text(compact_report(report))
        (folder/'metadata.json').write_text(json.dumps({'mode':args.mode,'iterations':args.iterations,
            'rows':64,'passed':True,'event':'cpu-clock:u','frequency':499,'os':platform.platform(),
            'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'dependency_manifest':(Path(__file__).resolve().parents[1]/'build.sig.zon').read_text(),
            'started_unix':started,'profiled_request_wall_ns':elapsed,'server_user_system_cpu_seconds':cpu,
            'control_directory':str(controls),'reopen_verified':durable,
            'scope':'Owned server user-CPU samples after seed; one psycopg client, simple protocol; no TLS/SCRAM; profiler overhead included. Wall minus CPU includes client/network/scheduling/waits and is not attributed to fsync. Process-crash reopen is not a power-loss test.'},indent=2)+'\n')
        print('Server profile complete:',folder,flush=True)
    finally:
        if recorder is not None and recorder.poll() is None:
            recorder.send_signal(signal.SIGINT);recorder.wait(timeout=20)
        os.close(ctl);os.close(reader)
    return 0


if __name__=='__main__': raise SystemExit(main())
