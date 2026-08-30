import os
import sys
import subprocess as spc
from typing import List, Optional
from util.errors import ConfigError, ExecutionError
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
    def _build_dockerfile(dockerfile_path: str, engine: str) -> str:
        from pathlib import Path
        import hashlib
        
        path = Path(dockerfile_path)
        if not path.exists():
            raise ConfigError(f"Dockerfile '{dockerfile_path}' not found.")
        
        with open(path, "rb") as f:
            content = f.read()
        
        hash_str = hashlib.sha256(content).hexdigest()[:12]
        image_name = f"run-sandbox-{hash_str}"
        
        res = spc.run([engine, "images", "-q", image_name], capture_output=True, text=True)
        if not res.stdout.strip():
            Printer.info(f"Building sandbox image '{image_name}' from {dockerfile_path}...")
            build_res = spc.run([engine, "build", "-t", image_name, "-f", dockerfile_path, str(path.parent)])
            if build_res.returncode != 0:
                raise ConfigError(f"Failed to build Dockerfile '{dockerfile_path}'.")
        return image_name

    @staticmethod
    def get_heuristic_image(cmd: List[str] = None) -> str:
        base_image = "ubuntu:latest"
        if cmd:
            exe = cmd[0].lower()
            if "gcc" in exe or "g++" in exe or "clang" in exe:
                base_image = "gcc:latest"
            elif "python" in exe or "pytest" in exe:
                base_image = "python:3-slim"
            elif "rustc" in exe or "cargo" in exe:
                base_image = "rust:latest"
            elif "java" in exe or "javac" in exe:
                base_image = "openjdk:latest"
            elif "go" in exe:
                base_image = "golang:latest"
            elif "node" in exe or "npm" in exe or "npx" in exe:
                base_image = "node:latest"
            elif "ruby" in exe:
                base_image = "ruby:latest"
        return base_image

    @staticmethod
    def wrap_command(cmd: List[str], net: bool = False, compiling: bool = False, sandbox_cfg: dict = {}) -> List[str]:
        if sys.platform == "linux" and not sandbox_cfg:
            if compiling:
                return cmd
            return NativeRestrictor.wrap_command(cmd, net)
            
        engine = ContainerSandbox._get_engine()
        cwd = os.getcwd()
        mount_mode = "rw" if compiling else "ro"
        
        container_cmd = [
            engine, "run", "--rm",
            "-v", f"{cwd}:{cwd}:{mount_mode}",
            "-v", "/tmp:/tmp",
            "-w", cwd
        ]
        
        if not net:
            container_cmd.extend(["--network", "none"])
            
        if sandbox_cfg.get("dockerfile"):
            base_image = ContainerSandbox._build_dockerfile(sandbox_cfg["dockerfile"], engine)
        elif sandbox_cfg.get("image"):
            base_image = sandbox_cfg["image"]
        else:
            base_image = ContainerSandbox.get_heuristic_image(cmd)
                
        container_cmd.append(base_image) 
        container_cmd.extend(cmd)
        
        return container_cmd


class ComposeSandbox:
    """Handles docker-compose execution environments."""
    
    @staticmethod
    def setup(compose_file: str):
        Printer.info(f"Starting docker-compose from {compose_file}...")
        res = spc.run(["docker", "compose", "-f", compose_file, "up", "-d"])
        if res.returncode != 0:
            raise ConfigError(f"Failed to start docker-compose using {compose_file}")
            
    @staticmethod
    def teardown(compose_file: str):
        Printer.info(f"Stopping docker-compose from {compose_file}...")
        spc.run(["docker", "compose", "-f", compose_file, "down"])
        
    @staticmethod
    def wrap_command(cmd: List[str], compose_file: str, service: str) -> List[str]:
        return ["docker", "compose", "-f", compose_file, "exec", "-w", os.getcwd(), service] + cmd


class PersistentSandbox:
    """Handles long-running sleeper containers for fast watch mode reloads."""
    
    _container_id: Optional[str] = None
    _engine: str = "docker"

    @staticmethod
    def start(engine: str, image: str, net: bool = False, cwd: str = ""):
        Printer.info(f"Starting persistent sandbox container ({image})...")
        cmd = [engine, "run", "-d", "--rm", "-v", f"{cwd}:{cwd}:rw", "-v", "/tmp:/tmp", "-w", cwd]
        if not net:
            cmd.extend(["--network", "none"])
        cmd.extend([image, "tail", "-f", "/dev/null"])
        
        res = spc.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise ConfigError(f"Failed to start persistent container: {res.stderr}")
            
        PersistentSandbox._container_id = res.stdout.strip()
        PersistentSandbox._engine = engine

    @staticmethod
    def stop():
        if PersistentSandbox._container_id:
            Printer.info("Stopping persistent sandbox container...")
            spc.run([PersistentSandbox._engine, "stop", PersistentSandbox._container_id], capture_output=True)
            PersistentSandbox._container_id = None
            
    @staticmethod
    def wrap_command(cmd: List[str]) -> List[str]:
        if not PersistentSandbox._container_id:
            raise ExecutionError("Persistent container is not running.")
        return [PersistentSandbox._engine, "exec", "-w", os.getcwd(), PersistentSandbox._container_id] + cmd
