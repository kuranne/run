"""Run a command with a non-elevated (LUA) Windows token so IsUserAnAdmin() == 0.

GitHub Actions ``windows-latest`` runners execute steps as an elevated
Administrator (``runneradmin``). Because ``run`` intentionally refuses to
compile/execute arbitrary code as root/Administrator unless ``--unsafe`` is
passed (which also disables suspicious-flag filtering), this helper drops
Administrator elevation via Win32 ``CreateRestrictedToken(LUA_TOKEN)`` and
updates ``TokenDefaultDacl`` / ``TokenOwner`` to the user's SID before spawning
``pytest`` so standard-user pipe/process creation and CLI security tests work.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _run_unelevated_windows(argv: list[str]) -> int:
    import ctypes
    from ctypes import byref, c_ubyte, c_void_p, sizeof, wintypes

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

    TOKEN_USER_CLASS = 1
    TOKEN_OWNER_CLASS = 4
    TOKEN_DEFAULT_DACL_CLASS = 6
    ACL_REVISION = 2
    GENERIC_ALL = 0x10000000
    WIN_LOCAL_SYSTEM_SID = 22
    SECURITY_MAX_SID_SIZE = 68
    ACL_BUFFER_SIZE = 512

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Sid", c_void_p),
            ("Attributes", wintypes.DWORD),
        ]

    class TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", SID_AND_ATTRIBUTES)]

    class TOKEN_OWNER(ctypes.Structure):
        _fields_ = [("Owner", c_void_p)]

    class TOKEN_DEFAULT_DACL(ctypes.Structure):
        _fields_ = [("DefaultDacl", c_void_p)]

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

    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL

    advapi32.SetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        c_void_p,
        wintypes.DWORD,
    ]
    advapi32.SetTokenInformation.restype = wintypes.BOOL

    advapi32.CreateWellKnownSid.argtypes = [
        wintypes.DWORD,
        c_void_p,
        c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.CreateWellKnownSid.restype = wintypes.BOOL

    advapi32.InitializeAcl.argtypes = [
        c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi32.InitializeAcl.restype = wintypes.BOOL

    advapi32.AddAccessAllowedAce.argtypes = [
        c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        c_void_p,
    ]
    advapi32.AddAccessAllowedAce.restype = wintypes.BOOL

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

        # Query the user SID so the unelevated token owns and can access its own pipes/handles.
        needed = wintypes.DWORD(0)
        advapi32.GetTokenInformation(
            restricted, TOKEN_USER_CLASS, None, 0, byref(needed)
        )
        if needed.value == 0:
            raise ctypes.WinError(ctypes.get_last_error())

        user_buf = (c_ubyte * needed.value)()
        if not advapi32.GetTokenInformation(
            restricted, TOKEN_USER_CLASS, user_buf, needed.value, byref(needed)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        user_sid = TOKEN_USER.from_buffer(user_buf).User.Sid

        sys_sid_buf = (c_ubyte * SECURITY_MAX_SID_SIZE)()
        sys_sid_len = wintypes.DWORD(SECURITY_MAX_SID_SIZE)
        if not advapi32.CreateWellKnownSid(
            WIN_LOCAL_SYSTEM_SID, None, sys_sid_buf, byref(sys_sid_len)
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        acl_buf = (c_ubyte * ACL_BUFFER_SIZE)()
        if not advapi32.InitializeAcl(acl_buf, ACL_BUFFER_SIZE, ACL_REVISION):
            raise ctypes.WinError(ctypes.get_last_error())
        if not advapi32.AddAccessAllowedAce(
            acl_buf, ACL_REVISION, GENERIC_ALL, user_sid
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not advapi32.AddAccessAllowedAce(
            acl_buf, ACL_REVISION, GENERIC_ALL, sys_sid_buf
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        default_dacl = TOKEN_DEFAULT_DACL(DefaultDacl=ctypes.addressof(acl_buf))
        if not advapi32.SetTokenInformation(
            restricted,
            TOKEN_DEFAULT_DACL_CLASS,
            byref(default_dacl),
            sizeof(default_dacl),
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        owner = TOKEN_OWNER(Owner=user_sid)
        if not advapi32.SetTokenInformation(
            restricted,
            TOKEN_OWNER_CLASS,
            byref(owner),
            sizeof(owner),
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
