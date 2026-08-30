import ctypes
import os
libc = ctypes.CDLL("/usr/lib/libc.dylib")
errorbuf = ctypes.c_char_p()
profile = b"no-network"
res = libc.sandbox_init(profile, 1, ctypes.byref(errorbuf))
if res != 0:
    print(errorbuf.value.decode('utf-8'))
else:
    print("Sandbox OK")
