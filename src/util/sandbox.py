import os
import sys
import shutil
import hashlib
import signal
import atexit
import subprocess as spc
from pathlib import Path
from typing import List, Optional, Dict, Any
from util.errors import ConfigError, ExecutionError
from util.output import Printer

class NativeRestrictor:
    """Handles native OS restriction (bwrap on Linux, fork/sandbox_init on macOS)."""
    
    @staticmethod
    def wrap_command(cmd: List[str], net: bool = False, cwd: Optional[str] = None, compiling: bool = False) -> List[str]:
        if sys.platform == "linux":
            if not shutil.which("bwrap"):
                raise ConfigError(
                    "Bubblewrap ('bwrap') binary not found in PATH. "
                    "Please install bubblewrap (bwrap) or use --sandbox with Docker/Podman."
                )
            actual_cwd = cwd or os.getcwd()
            bwrap_cmd = [
                "bwrap",
                "--ro-bind", "/", "/",
                "--dev", "/dev",
                "--proc", "/proc",
                "--tmpfs", "/tmp",
                "--unshare-user",
                "--unshare-ipc",
                "--unshare-pid",
                "--unshare-uts",
                "--die-with-parent",
                "--new-session",
            ]
            if not net:
                bwrap_cmd.append("--unshare-net")
            if compiling:
                bwrap_cmd.extend(["--bind", actual_cwd, actual_cwd])
            else:
                bwrap_cmd.extend(["--ro-bind", actual_cwd, actual_cwd])
            bwrap_cmd.extend(["--chdir", actual_cwd])
            bwrap_cmd.extend(cmd)
            return bwrap_cmd
        elif sys.platform == "darwin":
            # On macOS, handled internally via macos_preexec_fn
            return cmd
        else:
            raise ConfigError("--restrict is not supported on this OS. Use --sandbox instead.")

    @staticmethod
    def macos_preexec_fn():
        """Pre-execution function for subprocess on macOS to apply sandbox_init and rlimits safely."""
        import resource
        try:
            # Set CPU limit if appropriate; avoid RLIMIT_AS on 64-bit macOS as it crashes dyld
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
            except (ValueError, OSError):
                pass
            
            # Apply sandbox_init if possible (might fail due to SIP on newer macOS)
            try:
                import ctypes
                libc = ctypes.CDLL("/usr/lib/libc.dylib")
                if hasattr(libc, "sandbox_init"):
                    sandbox_init = libc.sandbox_init
                    sandbox_init.argtypes = [ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(ctypes.c_char_p)]
                    sandbox_init.restype = ctypes.c_int
                    
                    errorbuf = ctypes.c_char_p()
                    profile = b"no-write-except-temporary"
                    
                    res = sandbox_init(profile, 1, ctypes.byref(errorbuf))
                    if res != 0:
                        err_msg = errorbuf.value.decode('utf-8') if errorbuf.value else 'Unknown error'
                        print(f"[WARN] macOS sandbox_init failed (SIP?): {err_msg}", file=sys.stderr)
                        if hasattr(libc, "sandbox_free_error") and errorbuf.value:
                            libc.sandbox_free_error(errorbuf)
            except Exception:
                pass
        except Exception as e:
            print(f"[WARN] Sandbox setup failed: {e}", file=sys.stderr)


class ContainerSandbox:
    """Handles container-based sandboxing using Docker or Podman."""
    
    @staticmethod
    def _get_engine() -> str:
        # Check Docker first if responsive
        if shutil.which("docker"):
            try:
                res = spc.run(["docker", "info"], capture_output=True, timeout=3)
                if res.returncode == 0:
                    return "docker"
            except Exception:
                pass
        
        # Check Podman if Docker daemon is not running or Docker is missing
        if shutil.which("podman"):
            try:
                res = spc.run(["podman", "info"], capture_output=True, timeout=3)
                if res.returncode == 0:
                    return "podman"
            except Exception:
                pass
            
        # Fallback to --version if info check was skipped or mock environment
        if shutil.which("docker"):
            try:
                if spc.run(["docker", "--version"], capture_output=True).returncode == 0:
                    return "docker"
            except Exception:
                pass
        if shutil.which("podman"):
            try:
                if spc.run(["podman", "--version"], capture_output=True).returncode == 0:
                    return "podman"
            except Exception:
                pass
            
        raise ConfigError("No container engine (Docker/Podman) found. Please install one to use --sandbox.")

    @staticmethod
    def _build_dockerfile(dockerfile_path: str, engine: str) -> str:
        path = Path(dockerfile_path)
        if not path.exists():
            raise ConfigError(f"Dockerfile '{dockerfile_path}' not found.")
        
        with open(path, "rb") as f:
            content = f.read()
        
        # Resolve build context directory
        try:
            context_dir = str(Path.cwd()) if path.resolve().is_relative_to(Path.cwd().resolve()) else str(path.parent)
        except (ValueError, AttributeError):
            context_dir = str(path.parent)
        
        hash_input = content + str(path.resolve()).encode("utf-8")
        hash_str = hashlib.sha256(hash_input).hexdigest()[:12]
        image_name = f"run-sandbox-{hash_str}"
        
        res = spc.run([engine, "images", "-q", image_name], capture_output=True, text=True)
        if res.returncode != 0 or not res.stdout.strip():
            Printer.info(f"Building sandbox image '{image_name}' from {dockerfile_path}...")
            build_res = spc.run([engine, "build", "-t", image_name, "-f", str(path), context_dir])
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
    def wrap_command(cmd: List[str], net: bool = False, compiling: bool = False, sandbox_cfg: Optional[Dict[str, Any]] = None) -> List[str]:
        sandbox_cfg = sandbox_cfg or {}
        engine = ContainerSandbox._get_engine()
        cwd = os.getcwd()
        mount_mode = "rw" if compiling else "ro"
        
        container_cmd = [
            engine, "run", "--rm",
            "--security-opt=no-new-privileges",
            "--cap-drop=ALL",
            "-v", f"{cwd}:{cwd}:{mount_mode}",
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
    
    _active_files: List[str] = []
    _cleanup_registered: bool = False

    @classmethod
    def _register_cleanup(cls):
        if not cls._cleanup_registered:
            atexit.register(cls._cleanup_all)
            def _sig_handler(signum, frame):
                cls._cleanup_all()
                sys.exit(128 + signum)
            try:
                signal.signal(signal.SIGTERM, _sig_handler)
                signal.signal(signal.SIGINT, _sig_handler)
            except (ValueError, AttributeError):
                pass
            cls._cleanup_registered = True

    @classmethod
    def _cleanup_all(cls):
        for f in list(cls._active_files):
            try:
                cls.teardown(f)
            except Exception:
                pass

    @classmethod
    def setup(cls, compose_file: str):
        cls._register_cleanup()
        Printer.info(f"Starting docker-compose from {compose_file}...")
        res = spc.run(["docker", "compose", "-f", compose_file, "up", "-d"])
        if res.returncode != 0:
            raise ConfigError(f"Failed to start docker-compose using {compose_file}")
        if compose_file not in cls._active_files:
            cls._active_files.append(compose_file)
            
    @classmethod
    def teardown(cls, compose_file: str):
        try:
            if compose_file in cls._active_files:
                cls._active_files.remove(compose_file)
            spc.run(["docker", "compose", "-f", compose_file, "down"], capture_output=True)
        except Exception:
            pass
        
    @staticmethod
    def wrap_command(cmd: List[str], compose_file: str, service: str) -> List[str]:
        return ["docker", "compose", "-f", compose_file, "exec", "-w", os.getcwd(), service] + cmd


class PersistentSandbox:
    """Handles long-running sleeper containers for fast watch mode reloads."""
    
    _container_id: Optional[str] = None
    _engine: str = "docker"
    _cleanup_registered: bool = False

    @classmethod
    def _register_cleanup(cls):
        if not cls._cleanup_registered:
            atexit.register(cls.stop)
            def _sig_handler(signum, frame):
                cls.stop()
                sys.exit(128 + signum)
            try:
                signal.signal(signal.SIGTERM, _sig_handler)
                signal.signal(signal.SIGINT, _sig_handler)
            except (ValueError, AttributeError):
                pass
            cls._cleanup_registered = True

    @classmethod
    def start(cls, engine: str, image: str, net: bool = False, cwd: str = ""):
        cls._register_cleanup()
        actual_cwd = cwd or os.getcwd()
        Printer.info(f"Starting persistent sandbox container ({image})...")
        cmd = [
            engine, "run", "-d", "--rm",
            "--security-opt=no-new-privileges",
            "--cap-drop=ALL",
            "-v", f"{actual_cwd}:{actual_cwd}:rw",
            "-w", actual_cwd
        ]
        if not net:
            cmd.extend(["--network", "none"])
        cmd.extend([image, "tail", "-f", "/dev/null"])
        
        res = spc.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise ConfigError(f"Failed to start persistent container: {res.stderr}")
            
        cls._container_id = res.stdout.strip()
        cls._engine = engine

    @classmethod
    def stop(cls):
        if cls._container_id:
            Printer.info("Stopping persistent sandbox container...")
            cid = cls._container_id
            cls._container_id = None
            spc.run([cls._engine, "stop", cid], capture_output=True)
            
    @classmethod
    def wrap_command(cls, cmd: List[str]) -> List[str]:
        if not cls._container_id:
            raise ExecutionError("Persistent container is not running.")
        return [cls._engine, "exec", "-w", os.getcwd(), cls._container_id] + cmd
