"""Reconecta o controle do aplicativo ao mesmo túnel após reiniciar o servidor."""
import ctypes
import json
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

RECORD = '.prospectax_tunel_preservado.json'


class ExistingTunnel:
    def __init__(self, pid, expected_path, creation=None):
        self.handle = None
        if os.name != 'nt':
            raise OSError('Reconexão disponível no Windows.')
        self.pid = int(pid)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
            'GetExitCodeProcess': ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            'GetProcessTimes': ([wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)], wintypes.BOOL),
            'QueryFullProcessImageNameW': ([wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            'WaitForSingleObject': ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            'TerminateProcess': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            fn = getattr(self.kernel, name)
            fn.argtypes, fn.restype = arguments, result
        # Query, synchronize and terminate: terminate is only used by the existing admin stop action.
        self.handle = self.kernel.OpenProcess(0x1000 | 0x100000 | 0x0001, False, self.pid)
        if not self.handle:
            raise OSError('Não foi possível acessar o processo do túnel.')
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if not self.kernel.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(size)):
                raise OSError('Não foi possível identificar o túnel.')
            if os.path.normcase(str(Path(buffer.value).resolve())) != os.path.normcase(str(Path(expected_path).resolve())):
                raise OSError('O processo não corresponde ao túnel desta instalação.')
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not self.kernel.GetProcessTimes(self.handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
                raise OSError('Não foi possível verificar a criação do processo.')
            self.creation = (created.dwHighDateTime << 32) | created.dwLowDateTime
            if creation is not None and self.creation != int(creation):
                raise OSError('O processo registrado já foi substituído.')
            if self.poll() is not None:
                raise OSError('O túnel registrado já foi encerrado.')
        except Exception:
            self.close()
            raise

    def poll(self):
        status = self.kernel.WaitForSingleObject(self.handle, 0)
        if status == 258:
            return None
        if status != 0:
            raise OSError('Não foi possível consultar o estado do túnel.')
        result = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(result)):
            raise OSError('Não foi possível consultar a saída do túnel.')
        return result.value

    def terminate(self):
        if self.poll() is None and not self.kernel.TerminateProcess(self.handle, 1):
            raise OSError('Não foi possível encerrar o túnel.')

    kill = terminate

    def wait(self, timeout=None):
        millis = 0xFFFFFFFF if timeout is None else max(0, min(int(timeout * 1000), 0xFFFFFFFE))
        if self.kernel.WaitForSingleObject(self.handle, millis) == 258:
            raise subprocess.TimeoutExpired('cloudflared', timeout)
        return self.poll()

    def close(self):
        if getattr(self, 'handle', None):
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __del__(self):
        self.close()


def remember_tunnel(base, pid):
    if os.name != 'nt':
        return False
    try:
        base = Path(base)
        process = ExistingTunnel(pid, base / 'cloudflared.exe')
        try:
            record = {'pid': process.pid, 'creation': process.creation, 'port': int(os.environ.get('PROSPECTAX_PORT', '8765'))}
            target = base / RECORD
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(record), encoding='utf-8')
            os.replace(temporary, target)
        finally:
            process.close()
        return True
    except Exception:
        return False


def adopt_existing_tunnel(context):
    base = Path(context['BASE_DIR'])
    record_path = base / RECORD
    if os.name != 'nt' or not record_path.exists():
        return False
    try:
        record = json.loads(record_path.read_text(encoding='utf-8'))
        if record['port'] != int(context['PORT']):
            return False
        process = ExistingTunnel(record['pid'], base / 'cloudflared.exe', record['creation'])
        context['_LINK_ONLINE_FINAL_STATE'].update({
            'proc': process, 'last_error': '',
            'started_at': process.creation / 10000000 - 11644473600,
        })
        context['_lo_status']()
        return True
    except Exception:
        return False
