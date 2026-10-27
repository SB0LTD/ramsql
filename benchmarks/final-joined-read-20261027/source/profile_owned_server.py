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
binary=Path(sys.argv[1] if len(sys.argv)>1 else '.sig-cache/star-server-fast/bin/ramsql.exe').resolve()
report_path=Path(sys.argv[2] if len(sys.argv)>2 else '.sig-cache/star-main-thread-profile.json')
caps=json.loads(subprocess.check_output([str(binary),'--capabilities']))
with Server(binary,max_clients=1,extra_arguments=arguments(caps,100000,32,1)) as server:
 pg=seed(server,100000,caps);expected=model(100000);pg.ok(QUERY,expected)
 process=ok(open_proc(0x410,False,server.process.pid));thread=ok(open_thread(0x4a,False,main_thread(server.process.pid)))
 raw=Counter()
 try:
  memory=c.create_string_buffer(1232+15);address=(c.addressof(memory)+15)&~15
  def work():
   for _ in range(400):pg.ok(QUERY,expected)
  with ThreadPoolExecutor(max_workers=1) as pool:
   future=pool.submit(work)
   while not future.done() and sum(raw.values())<2500:
    previous=suspend(thread)
    if previous==0xffffffff:raise c.WinError(c.get_last_error())
    try:
     c.c_uint32.from_address(address+48).value=0x100001
     ok(context(thread,address));rip=u64.from_address(address+248).value
     raw[rip]+=1
    finally:
     if resume(thread)==0xffffffff:raise c.WinError(c.get_last_error())
    time.sleep(.002)
   future.result()
  symopts(0x80216);ok(syminit(process,str(binary.parent),True))
  try:
   samples=Counter();lines=Counter()
   for rip,count in raw.items():
    storage=c.create_string_buffer(c.sizeof(Symbol)+4096);symbol=c.cast(storage,c.POINTER(Symbol));symbol.contents.size=c.sizeof(Symbol);symbol.contents.max_name=4095
    displacement=u64()
    name=hex(rip)
    if symaddr(process,rip,c.byref(displacement),symbol):name=c.string_at(c.addressof(storage)+Symbol.name.offset,symbol.contents.name_len).decode('utf-8','replace')
    samples[name]+=count
    info=Line();info.size=c.sizeof(info);offset=u32()
    if lineaddr(process,rip,c.byref(offset),c.byref(info)):
     file=info.file.decode('utf-8','replace') if info.file else ''
     lines[(name,file,info.line)]+=count
   report={'schema':1,'method':'Wall-time sampled user instruction pointers from the owned child main thread; Suspend/GetThreadContext/Resume with guaranteed resume; not PMU CPU samples or full stacks','query':QUERY,'rows':100000,'work_rows':32,'checked_queries':400,'sample_interval_ms':2,'samples':sum(raw.values()),'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'symbols':[{'name':name,'samples':n} for name,n in samples.most_common(40)],'lines':[{'name':name,'file':file,'line':line,'samples':n} for (name,file,line),n in lines.most_common(40)]}
   report_path.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:report[k] for k in ['method','samples','symbols']},indent=2))
  finally:cleanup(process)
 finally:
  close(thread);close(process);pg.close()
