"""Low-rate process-image inventory for benchmark validity, not code profiling."""
import ctypes as c, threading, time
from ctypes import wintypes as w

class Entry(c.Structure):
    _fields_=[('size',w.DWORD),('usage',w.DWORD),('pid',w.DWORD),('heap',c.c_size_t),('module',w.DWORD),('threads',w.DWORD),('parent',w.DWORD),('priority',w.LONG),('flags',w.DWORD),('image',w.WCHAR*260)]
k=c.WinDLL('kernel32',use_last_error=True)
k.CreateToolhelp32Snapshot.argtypes=[w.DWORD,w.DWORD];k.CreateToolhelp32Snapshot.restype=w.HANDLE
k.Process32FirstW.argtypes=[w.HANDLE,c.POINTER(Entry)];k.Process32FirstW.restype=w.BOOL
k.Process32NextW.argtypes=[w.HANDLE,c.POINTER(Entry)];k.Process32NextW.restype=w.BOOL
k.CloseHandle.argtypes=[w.HANDLE];k.CloseHandle.restype=w.BOOL
names={'sig.exe','zig.exe','clang.exe','clang++.exe','cl.exe','link.exe','lld-link.exe','msbuild.exe','ninja.exe','cmake.exe','sig_build_runner.exe','postgres.exe','irdb.exe','irdbserver.exe','mysqld.exe','sqlservr.exe'}
def inventory():
    handle=k.CreateToolhelp32Snapshot(2,0)
    if handle==c.c_void_p(-1).value:raise c.WinError(c.get_last_error())
    try:
        e=Entry();e.size=c.sizeof(e);present=k.Process32FirstW(handle,c.byref(e));found=[]
        while present:
            image=e.image.lower()
            if image in names:found.append(dict(pid=e.pid,image=image))
            e.size=c.sizeof(e);present=k.Process32NextW(handle,c.byref(e))
        return found
    finally:k.CloseHandle(handle)

class Watch:
    def __init__(self,run):
        self.stop=threading.Event();self.thread=None
        self.result=dict(run=run,interval_seconds=2,checks=0,observed=[],errors=[],limitations='Periodic image-name inventory can miss short processes, treats any Sig process conservatively, and does not observe every background workload. No process is stopped or reprioritized; no command lines are captured.')
    def sample(self):
        self.result['checks']+=1
        try:
            stamp=int(time.time()*1000)
            for item in inventory():self.result['observed'].append(dict(at_epoch_ms=stamp,**item))
        except Exception as err:self.result['errors'].append(type(err).__name__+': '+str(err))
    def loop(self):
        while not self.stop.wait(2):self.sample()
    def __enter__(self):
        self.sample();self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start();return self
    def __exit__(self,*args):
        self.stop.set();self.thread.join();self.sample()
