import os
import sys
import subprocess as spc
from typing import List, Optional
from util.errors import ConfigError
from util.output import Printer

class NativeRestrictor:
    """Handles native OS restriction (bwrap on Linux, fork/sandbox_init on macOS)."""
    
    @staticmethod
    def wrap_command(cmd: List[str], net: bool = False) -> List[str]:
        if sys.platform == "linux":
            bwrap_cmd = [
                "bwrap",
                "--ro-bind", "/", "/",
                "--dev", "/dev",
                "--proc", "/proc",
                "--bind", "/tmp", "/tmp",
                "--unshare-pid", "--unshare-ipc", "--unshare-uts"
            ]
            if not net:
                bwrap_cmd.append("--unshare-net")
            bwrap_cmd.extend(cmd)
            return bwrap_cmd
        elif sys.platform == "darwin":
            # On macOS, handled internally via execute_macos
            return cmd
        else:
            raise ConfigError("--restrict is not supported on this OS. Use --sandbox instead.")

    @staticmethod
    def macos_preexec_fn():
        """Pre-execution function for subprocess on macOS to apply sandbox_init and rlimits."""
        import ctypes
        import resource
        
        try:
            # Set basic rlimits: 512MB memory, 60s CPU time
            try:
                resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
            except ValueError:
                pass
            
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
            except ValueError:
                pass
            
            # Apply sandbox_init if possible (might fail due to SIP on newer macOS)
            libc = ctypes.CDLL("/usr/lib/libc.dylib")
            sandbox_init = libc.sandbox_init
            sandbox_init.argtypes = [ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(ctypes.c_char_p)]
            sandbox_init.restype = ctypes.c_int
            
            errorbuf = ctypes.c_char_p()
            profile = b"no-write-except-temporary"
            
            res = sandbox_init(profile, 1, ctypes.byref(errorbuf))
            if res != 0:
                err_msg = errorbuf.value.decode('utf-8') if errorbuf.value else 'Unknown error'
                # Do not exit; SIP might block it. Just warn.
                print(f"[WARN] macOS sandbox_init failed (SIP?): {err_msg}", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Sandbox setup failed: {e}", file=sys.stderr)


class ContainerSandbox:
    """Handles container-based sandboxing using Docker or Podman."""
    
    @staticmethod
    def _get_engine() -> str:
        try:
            if spc.run(["docker", "--version"], capture_output=True).returncode == 0:
                return "docker"
        except FileNotFoundError:
            pass
        
        try:
            if spc.run(["podman", "--version"], capture_output=True).returncode == 0:
                return "podman"
        except FileNotFoundError:
            pass
            
        raise ConfigError("No container engine (Docker/Podman) found. Please install one to use --sandbox.")

    @staticmethod
    def wrap_command(cmd: List[str], net: bool = False) -> List[str]:
        if sys.platform == "linux":
            # On Linux, --sandbox uses bwrap natively
            return NativeRestrictor.wrap_command(cmd, net)
            
        engine = ContainerSandbox._get_engine()
        cwd = os.getcwd()
        
        container_cmd = [
            engine, "run", "--rm",
            "-v", f"{cwd}:{cwd}:ro",
            "-v", "/tmp:/tmp",
            "-w", cwd
        ]
        
        if not net:
            container_cmd.extend(["--network", "none"])
            
        # Using ubuntu as a generic base image. Users may need to pull it.
        container_cmd.append("ubuntu:latest") 
        container_cmd.extend(cmd)
        
        return container_cmd
