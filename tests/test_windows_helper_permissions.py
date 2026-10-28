"""Real Windows medium/high file exchange; never invokes firmware tools."""
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows token/ACL integration test')


@contextmanager
def medium_token():
    from ctypes import wintypes as w
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    class Label(ctypes.Structure):
        _fields_ = [('sid', ctypes.c_void_p), ('attributes', w.DWORD)]
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    adv.OpenProcessToken.restype = w.BOOL
    adv.DuplicateTokenEx.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(w.HANDLE)]
    adv.DuplicateTokenEx.restype = w.BOOL
    adv.ConvertStringSidToSidW.argtypes = [w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    adv.ConvertStringSidToSidW.restype = w.BOOL
    adv.SetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    adv.SetTokenInformation.restype = w.BOOL
    adv.GetLengthSid.argtypes = [ctypes.c_void_p]
    adv.GetLengthSid.restype = w.DWORD
    adv.SetThreadToken.argtypes = [ctypes.c_void_p, w.HANDLE]
    adv.SetThreadToken.restype = w.BOOL
    adv.RevertToSelf.restype = w.BOOL
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    original, token, sid = w.HANDLE(), w.HANDLE(), ctypes.c_void_p()
    def check(ok):
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        check(adv.OpenProcessToken(kernel.GetCurrentProcess(), 0x000A, ctypes.byref(original)))
        check(adv.DuplicateTokenEx(original, 0xF01FF, None, 2, 2, ctypes.byref(token)))
        check(adv.ConvertStringSidToSidW('S-1-16-8192', ctypes.byref(sid)))
        label = Label(sid, 0x20)
        check(adv.SetTokenInformation(token, 25, ctypes.byref(label), ctypes.sizeof(label) + adv.GetLengthSid(sid)))
        check(adv.SetThreadToken(None, token))
        try:
            yield
        finally:
            check(adv.RevertToSelf())
    finally:
        if sid: kernel.LocalFree(sid)
        if token: kernel.CloseHandle(token)
        if original: kernel.CloseHandle(original)


def test_real_elevated_helper_preserves_medium_caller_reply_access(tmp_path):
    if not ctypes.windll.shell32.IsUserAnAdmin():
        pytest.skip('Run as administrator to exercise both integrity levels without a UAC prompt')
    # Only this pytest-owned directory is relabelled, allowing the medium
    # impersonation token to create files even on a high-integrity CI runner.
    subprocess.run(['icacls', str(tmp_path), '/setintegritylevel', '(OI)(CI)M'], check=True, capture_output=True)
    from x99toolkit.fpt_elevation import read_result
    request = tmp_path / 'request.json'
    reply = tmp_path / 'result.json'
    with medium_token():
        request.write_text(json.dumps({'action': 'check-transport', 'backup_directory': str(tmp_path)}), encoding='utf-8')
        reply.touch()
        (tmp_path / 'events.jsonl').touch()
        identity = reply.stat().st_ino
    entry = Path(__file__).resolve().parents[1] / 'SenorX99-CLI.py'
    process = subprocess.run([sys.executable, str(entry), '--fpt-helper', str(request)], capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stdout + process.stderr
    with medium_token():
        envelope = read_result(reply, process.returncode)
        assert envelope['result']['helper_elevated'] is True
        assert envelope['result']['transport_verified'] is True
        assert reply.stat().st_ino == identity
        assert 'Elevated helper transport test' in (tmp_path / 'events.jsonl').read_text()
        # Also exercise caller cleanup after the elevated process has exited.
        for path in (reply, request, tmp_path / 'events.jsonl'):
            path.unlink()
