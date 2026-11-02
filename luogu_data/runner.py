"""Bounded subprocess runner. Resource limits are not a security sandbox."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from .common import PipelineError


class WindowsJob:
    def __init__(self, memory_mb: int):
        import ctypes as c
        from ctypes import wintypes as w
        class IO(c.Structure):
            _fields_ = [(n, c.c_ulonglong) for n in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
        class BASIC(c.Structure):
            _fields_ = [('PerProcessUserTimeLimit', c.c_longlong), ('PerJobUserTimeLimit', c.c_longlong), ('LimitFlags', w.DWORD), ('MinimumWorkingSetSize', c.c_size_t), ('MaximumWorkingSetSize', c.c_size_t), ('ActiveProcessLimit', w.DWORD), ('Affinity', c.c_size_t), ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]
        class EXT(c.Structure):
            _fields_ = [('BasicLimitInformation', BASIC), ('IoInfo', IO), ('ProcessMemoryLimit', c.c_size_t), ('JobMemoryLimit', c.c_size_t), ('PeakProcessMemoryUsed', c.c_size_t), ('PeakJobMemoryUsed', c.c_size_t)]
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.k.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        self.k.CreateJobObjectW.restype = w.HANDLE
        self.k.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        self.k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.k.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        self.k.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.k.CreateJobObjectW(None, None)
        limits = EXT()
        limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x200  # KILL_ON_JOB_CLOSE, JOB_MEMORY
        limits.JobMemoryLimit = memory_mb * 1024 * 1024
        if not self.handle or not self.k.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            self.close()
            raise PipelineError('Windows Job Object 初始化失败，拒绝无资源限制运行')

    def attach(self, process):
        if not self.k.AssignProcessToJobObject(self.handle, int(process._handle)):
            process.kill()
            process.wait()
            raise PipelineError('无法将进程加入 Windows Job Object')

    def close(self):
        if getattr(self, 'handle', None):
            self.k.TerminateJobObject(self.handle, 1)
            self.k.CloseHandle(self.handle)
            self.handle = None


def run(command: list[str], *, cwd: Path, input_text='', timeout=10.0,
        memory_mb=512, max_bytes=24 * 1024 * 1024) -> tuple[str, float]:
    if timeout <= 0 or memory_mb < 16:
        raise PipelineError('无效的运行资源限制')
    # Generated programs never inherit model credentials.
    child_env = {k: v for k, v in os.environ.items()
                 if not any(x in k.upper() for x in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL'))}
    child_env['PYTHONUTF8'] = '1'
    job = WindowsJob(memory_mb) if os.name == 'nt' else None
    def limits():
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024**2,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (max(1, int(timeout) + 1),) * 2)
    with tempfile.TemporaryFile() as inp, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        inp.write(input_text.encode('utf-8'))
        inp.seek(0)
        started = time.perf_counter()
        proc = None
        try:
            proc = subprocess.Popen(command, cwd=cwd, stdin=inp, stdout=out, stderr=err,
                                    env=child_env, start_new_session=os.name != 'nt',
                                    preexec_fn=limits if os.name != 'nt' else None)
            if job:
                job.attach(proc)
            while proc.poll() is None:
                if time.perf_counter() - started > timeout:
                    raise PipelineError(f'运行超时（{timeout:g}s）：{Path(command[0]).name}')
                if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > max_bytes:
                    raise PipelineError('子进程输出超过大小限制')
                time.sleep(0.01)
            elapsed = time.perf_counter() - started
            out.seek(0)
            err.seek(0)
            raw, diagnostic = out.read(max_bytes + 1), err.read(max_bytes + 1)
            if len(raw) + len(diagnostic) > max_bytes:
                raise PipelineError('子进程输出超过大小限制')
            if proc.returncode != 0:
                raise PipelineError(f'{Path(command[0]).name} 退出码 {proc.returncode}:\n' + diagnostic.decode('utf-8', errors='replace')[-6000:])
            try:
                return raw.decode('utf-8'), elapsed
            except UnicodeDecodeError as exc:
                raise PipelineError('输出必须为 UTF-8 文本') from exc
        finally:
            if proc is not None:
                if os.name != 'nt':
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif proc.poll() is None:
                    proc.kill()
                proc.wait()
            if job:
                job.close()
