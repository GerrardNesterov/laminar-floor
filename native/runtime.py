"""Reclaim inactive payloads belonging to the single-process application loader."""
import ctypes,json,os,re,shutil,sys,tempfile,time,stat
from pathlib import Path

def maintain_payload():
    if sys.platform!='win32' or not (hasattr(sys.modules['__main__'],'__compiled__') or getattr(sys,'frozen',False)):return
    root=(Path(tempfile.gettempdir())/'LaminarFloorWin81').resolve()
    current=Path(__file__).resolve().parents[1]
    if current.parent!=root or not current.name.startswith('run_'):return
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    def started(pid):
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return None if ctypes.get_last_error()==87 else 'unknown'
        try:
            stamps=[wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in stamps]):return 'unknown'
            return (stamps[0].dwHighDateTime<<32)|stamps[0].dwLowDateTime
        finally:kernel.CloseHandle(handle)
    (current/'.laminar-payload.json').write_text(json.dumps({'pid':os.getpid(),'started':started(os.getpid())}),encoding='utf-8')
    for path in root.glob('run_*'):
        if path==current or path.is_symlink():continue
        try:
            if getattr(path.lstat(),'st_file_attributes',0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:continue
            marker=path/'.laminar-payload.json'
            if not marker.is_file():
                # Failed startup before Python could write a marker. Only our PID/time/random names.
                match=re.fullmatch(r'run_(\d+)_[A-Za-z0-9_]+',path.name)
                if not match or time.time()-path.stat().st_mtime<7*86400 or started(int(match[1])) is not None:continue
            else:
                data=json.loads(marker.read_text(encoding='utf-8'));pid=data['pid']
                if not isinstance(pid,int) or pid<=0:continue
                live=started(pid)
                recorded=data.get('started')
                if live=='unknown' or (live is not None and (not isinstance(recorded,int) or live==recorded)):continue
            shutil.rmtree(path)
        except (OSError,ValueError,KeyError,TypeError):pass


def configure_platform_queries():
    """Python3.10 platform.win32_ver otherwise launches cmd.exe for `ver`.

    Use the already available Windows version API so scientific libraries can
    query platform information without violating the one-process contract.
    """
    if sys.platform != 'win32':
        return
    import platform
    def windows_version(system='', release='', version='', supported_platforms=('win32', 'win16', 'dos')):
        if sys.platform not in supported_platforms:
            return system, release, version
        current = sys.getwindowsversion()
        numbers = current.platform_version or current[:3]
        return 'Windows', '', '.'.join(map(str, numbers))
    platform._syscmd_ver = windows_version
