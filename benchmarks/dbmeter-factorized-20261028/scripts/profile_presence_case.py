import ctypes as c
from ctypes import wintypes as w
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,os,platform,sys,time
from pathlib import Path
sys.path.insert(0,'tools')
from test_server import Server
from test_streamed_joins import QUERY,arguments,model,seed
import subprocess
assert os.name=='nt' and c.sizeof(c.c_void_p)==8 and platform.machine().upper()=='AMD64'
k=c.WinDLL('kernel32',use_last_error=True);d=c.WinDLL('dbghelp',use_last_error=True)
def bind(lib,name,args,ret):
 f=getattr(lib,name);f.argtypes=args;f.restype=ret;return f
def ok(value):
 if not value:raise c.WinError(c.get_last_error())
 return value
handle=c.c_void_p;u64=c.c_uint64;u32=c.c_uint32
snap=bind(k,'CreateToolhelp32Snapshot',[u32,u32],handle)
close=bind(k,'CloseHandle',[handle],w.BOOL)
open_thread=bind(k,'OpenThread',[u32,w.BOOL,u32],handle)
open_proc=bind(k,'OpenProcess',[u32,w.BOOL,u32],handle)
suspend=bind(k,'SuspendThread',[handle],u32);resume=bind(k,'ResumeThread',[handle],u32)
context=bind(k,'GetThreadContext',[handle,c.c_void_p],w.BOOL)
times=bind(k,'GetThreadTimes',[handle,*([c.POINTER(u64)]*4)],w.BOOL)
class Entry(c.Structure):
 _fields_=[('size',u32),('usage',u32),('tid',u32),('pid',u32),('base',c.c_int32),('delta',c.c_int32),('flags',u32)]
first=bind(k,'Thread32First',[handle,c.POINTER(Entry)],w.BOOL);next_thread=bind(k,'Thread32Next',[handle,c.POINTER(Entry)],w.BOOL)
def main_thread(pid):
 snapshot=ok(snap(4,0));entries=[]
 try:
  e=Entry();e.size=c.sizeof(e);present=first(snapshot,c.byref(e))
  while present:
   if e.pid==pid:
    th=ok(open_thread(0x40,False,e.tid))
    try:
     clocks=[u64() for _ in range(4)];ok(times(th,*[c.byref(t) for t in clocks]));entries.append((clocks[0].value,e.tid))
    finally:close(th)
   e.size=c.sizeof(e);present=next_thread(snapshot,c.byref(e))
 finally:close(snapshot)
 assert entries
 return min(entries)[1]
class Symbol(c.Structure):
 _fields_=[('size',u32),('type',u32),('reserved',u64*2),('index',u32),('symbol_size',u32),('module',u64),('flags',u32),('value',u64),('address',u64),('register',u32),('scope',u32),('tag',u32),('name_len',u32),('max_name',u32),('name',c.c_char*1)]
class Line(c.Structure):
 _fields_=[('size',u32),('key',c.c_void_p),('line',u32),('file',c.c_char_p),('address',u64)]
assert c.sizeof(Symbol)==88 and Symbol.name.offset==84
symopts=bind(d,'SymSetOptions',[u32],u32)
syminit=bind(d,'SymInitializeW',[handle,c.c_wchar_p,w.BOOL],w.BOOL)
symaddr=bind(d,'SymFromAddr',[handle,u64,c.POINTER(u64),c.POINTER(Symbol)],w.BOOL)
lineaddr=bind(d,'SymGetLineFromAddr64',[handle,u64,c.POINTER(u32),c.POINTER(Line)],w.BOOL)
cleanup=bind(d,'SymCleanup',[handle],w.BOOL)


def server_threads(pid):
 snapshot=ok(snap(4,0));entries=[]
 try:
  e=Entry();e.size=c.sizeof(e);present=first(snapshot,c.byref(e))
  while present:
   if e.pid==pid:entries.append(e.tid)
   e.size=c.sizeof(e);present=next_thread(snapshot,c.byref(e))
 finally:close(snapshot)
 return entries
def clocks(thread):
 values=[u64() for _ in range(4)];ok(times(thread,*[c.byref(t) for t in values]));return sum(v.value for v in values[2:])/1e7
mode=sys.argv[1];case=sys.argv[2];assert mode in ('on','off') and case in ('q5_high_value_customers','q6_store_category_mix')
binary=Path(f'.sig-cache/dbmeter-presence-{mode}-fast/bin/ramsql.exe').resolve()
root=Path('../dbMeter').resolve();base=Path(f'.sig-cache/dbmeter-presence-{mode}-{case}-profile').resolve();base.mkdir(exist_ok=False)
args=['--workers','8','--table-rows','5,200,20,1000,50000,5000000,1,1','--table-versions','10,400,40,2000,100000,5065536,2,2','--work-rows','65536','--result-rows','1024']
with Server(binary,max_clients=12,idle_ms=300000,extra_arguments=args) as server:
 java=['java','-Xms32m','-Xmx256m','-jar',str(root/'build/dbmeter.jar')]
 common=['--skip-setup','-c',str(root/'examples/sales-agg/ramsql.properties'),'--set',f'jdbc.url=jdbc:postgresql://127.0.0.1:{server.port}/ramsql']
 with (base/'setup.log').open('w') as log:
  subprocess.run([str(root/'tools/postgres18/pgsql/bin/psql.exe'),'-h','127.0.0.1','-p',str(server.port),'-U','postgres','-d','ramsql','-q','-v','ON_ERROR_STOP=1','-f',str(Path('.sig-cache/dbmeter-full/ramsql_load.sql').resolve())],stdout=log,stderr=subprocess.STDOUT,check=True,env=dict(os.environ,PGSSLMODE='disable'))
  subprocess.run(java+['check',*common,'--only','a_totals,q5_high_value_customers,q6_store_category_mix'],cwd=root,stdout=log,stderr=subprocess.STDOUT,check=True)
 process=ok(open_proc(0x410,False,server.process.pid));handles={};counts={};cpu_before={};by_slice={}
 try:
  main=main_thread(server.process.pid)
  for tid in server_threads(server.process.pid):
   if tid==main:continue
   handles[tid]=ok(open_thread(0x4a,False,tid));counts[tid]=Counter();cpu_before[tid]=clocks(handles[tid])
  assert handles
  memory=c.create_string_buffer(1232+15);address=(c.addressof(memory)+15)&~15
  def work():
   with (base/'jdbc.log').open('w') as log:
    subprocess.run(java+['load',*common,'--only',case,'--set','load.threads=8','--set','load.warmup=15s','--set','load.duration=60s','--out',str(base/'jdbc')],cwd=root,stdout=log,stderr=subprocess.STDOUT,check=True)
  began=time.monotonic()
  with ThreadPoolExecutor(max_workers=1) as pool:
   future=pool.submit(work)
   while not future.done():
    for tid,thread in handles.items():
     previous=suspend(thread)
     if previous==0xffffffff:raise c.WinError(c.get_last_error())
     try:
      c.c_uint32.from_address(address+48).value=0x100001
      ok(context(thread,address));rip=u64.from_address(address+248).value;counts[tid][rip]+=1;bucket=int((time.monotonic()-began)//10);by_slice.setdefault(bucket,Counter())[rip]+=1
     finally:
      if resume(thread)==0xffffffff:raise c.WinError(c.get_last_error())
    time.sleep(.01)
   future.result()
  wall=time.monotonic()-began;thread_cpu={tid:clocks(th)-cpu_before[tid] for tid,th in handles.items()}
  raw=Counter()
  for values in counts.values():raw.update(values)
  symopts(0x80216);ok(syminit(process,str(binary.parent),True))
  try:
   samples=Counter();lines=Counter();addresses=[]
   for rip,count in raw.items():
    storage=c.create_string_buffer(c.sizeof(Symbol)+4096);symbol=c.cast(storage,c.POINTER(Symbol));symbol.contents.size=c.sizeof(Symbol);symbol.contents.max_name=4095
    displacement=u64();name=hex(rip)
    if symaddr(process,rip,c.byref(displacement),symbol):name=c.string_at(c.addressof(storage)+Symbol.name.offset,symbol.contents.name_len).decode('utf-8','replace')
    addresses.append(dict(name=name,samples=count,rip=hex(rip),module_offset=hex(rip-symbol.contents.module) if symbol.contents.module else None,symbol_offset=hex(rip-symbol.contents.address) if symbol.contents.address else None));samples[name]+=count;info=Line();info.size=c.sizeof(info);offset=u32()
    if lineaddr(process,rip,c.byref(offset),c.byref(info)):
     file=info.file.decode('utf-8','replace') if info.file else '';lines[(name,file,info.line)]+=count
   report=dict(schema=1,case=case,filter_mode=mode,ten_second_instruction_slices={str(bucket):{hex(rip):count for rip,count in values.items()} for bucket,values in by_slice.items()},diagnostic_only=True,method='10ms wall-time instruction-pointer samples of every owned non-main server thread (including reactor and runtime workers) during original full5M JDBC case (recorded in case field). Threads resumed in finally. Includes ramp/warmup/waiting; not PMU CPU events, cycle attribution or a throughput comparison. No builds/reference services run concurrently.',binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),rows=5000000,workers=8,clients=8,wall_seconds=wall,threads=[dict(tid=tid,cpu_seconds=thread_cpu[tid],samples=sum(counts[tid].values())) for tid in handles],samples=sum(raw.values()),instruction_addresses=sorted(addresses,key=lambda item:item['samples'],reverse=True)[:160],symbols=[dict(name=name,samples=n) for name,n in samples.most_common(40)],lines=[dict(name=name,file=file,line=line,samples=n) for (name,file,line),n in lines.most_common(40)])
   (base/'profile.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report['symbols'][:12],indent=2))
  finally:cleanup(process)
 finally:
  for thread in handles.values():close(thread)
  close(process)
