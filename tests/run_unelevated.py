"""Run a command with a non-elevated (LUA) Windows token so IsUserAnAdmin() == 0.

GitHub Actions ``windows-latest`` runners execute steps as an elevated
Administrator (``runneradmin``). Because ``run`` intentionally refuses to
compile/execute arbitrary code as root/Administrator unless ``--unsafe`` is
passed (which also disables suspicious-flag filtering), this helper drops
Administrator elevation via Win32 ``CreateRestrictedToken(LUA_TOKEN)`` before
spawning ``pytest`` so all security and CLI tests run under standard-user
privileges with inherited stdio.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _run_unelevated_windows(argv: list[str]) -> int:
    import ctypes
    from ctypes import byref, c_void_p, sizeof, wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    TOKEN_ASSIGN_PRIMARY = 0x0001
    TOKEN_DUPLICATE = 0x0002
    TOKEN_QUERY = 0x0008
    TOKEN_ADJUST_DEFAULT = 0x0080
    TOKEN_ADJUST_SESSIONID = 0x0100
    TOKEN_ACCESS = (
        TOKEN_ASSIGN_PRIMARY
        | TOKEN_DUPLICATE
        | TOKEN_QUERY
        | TOKEN_ADJUST_DEFAULT
        | TOKEN_ADJUST_SESSIONID
    )
    LUA_TOKEN = 0x0004
    INFINITE = 0xFFFFFFFF

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD

    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL

    advapi32.CreateRestrictedToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        c_void_p,
        wintypes.DWORD,
        c_void_p,
        wintypes.DWORD,
        c_void_p,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.CreateRestrictedToken.restype = wintypes.BOOL

    advapi32.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        c_void_p,
        c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    advapi32.CreateProcessAsUserW.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    restricted = wintypes.HANDLE()
    pi = PROCESS_INFORMATION()

    try:
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), TOKEN_ACCESS, byref(token)
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        if not advapi32.CreateRestrictedToken(
            token, LUA_TOKEN, 0, None, 0, None, 0, None, byref(restricted)
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        si = STARTUPINFOW()
        si.cb = sizeof(STARTUPINFOW)

        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        if not advapi32.CreateProcessAsUserW(
            restricted,
            None,
            cmdline,
            None,
            None,
            True,
            0,
            None,
            None,
            byref(si),
            byref(pi),
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        kernel32.WaitForSingleObject(pi.hProcess, INFINITE)
        exit_code = wintypes.DWORD(1)
        if not kernel32.GetExitCodeProcess(pi.hProcess, byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(exit_code.value)
    finally:
        for handle in (pi.hThread, pi.hProcess, restricted, token):
            if handle:
                kernel32.CloseHandle(handle)


def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <command> [args...]", file=sys.stderr)
        return 2

    cmd = sys.argv[1:]
    if cmd[0] == "-m":
        cmd = [sys.executable, *cmd]

    if os.name != "nt":
        return subprocess.run(cmd, check=False).returncode

    return _run_unelevated_windows(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
