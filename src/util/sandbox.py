import re
import os
import sys
import uuid
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
                "--tmpfs", "/home",
                "--tmpfs", "/root",
                "--tmpfs", "/run",
                "--tmpfs", "/sys",
                "--unshare-user",
                "--unshare-ipc",
                "--unshare-pid",
                "--unshare-uts",
                "--unshare-cgroup-try",
                "--die-with-parent",
                "--new-session",
            ]
            if not net:
                bwrap_cmd.append("--unshare-net")
            if compiling:
                bwrap_cmd.extend(["--bind", actual_cwd, actual_cwd])
                cache_dir = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "run_kuranne"
                bwrap_cmd.extend(["--bind-try", str(cache_dir), str(cache_dir)])
            else:
                bwrap_cmd.extend(["--ro-bind", actual_cwd, actual_cwd])
            bwrap_cmd.extend(["--chdir", actual_cwd])
            bwrap_cmd.append("--")
            bwrap_cmd.extend(cmd)
            return bwrap_cmd
        elif sys.platform == "darwin":
            raise ConfigError(
                "--restrict is only supported on Linux (via Bubblewrap). "
                "On macOS, please use --sandbox (Docker/Podman) for isolation."
            )
        else:
            raise ConfigError("--restrict is only supported on Linux (via Bubblewrap). Use --sandbox instead.")


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

    IMAGE_NAME_PATTERN = re.compile(
        r"^[a-zA-Z0-9]+(?:[._-][a-zA-Z0-9]+)*(?::[0-9]+)?(?:/[a-zA-Z0-9._-]+)*(?::[a-zA-Z0-9_.-]+)?(?:@[a-zA-Z0-9]+:[a-fA-F0-9]+)?$"
    )

    @classmethod
    def validate_image_name(cls, image: str) -> str:
        """
        Validate container image name to prevent option injection or invalid references.

        Args:
            image (str): Candidate container image string.

        Returns:
            str: Validated image name.

        Raises:
            ConfigError: If image name is invalid or begins with '-'.
        """
        if not image or not isinstance(image, str):
            raise ConfigError("Container image name cannot be empty.")
        clean_image = image.strip()
        if clean_image.startswith("-"):
            raise ConfigError(f"Invalid container image '{image}': Image name cannot start with '-'.")
        if any(c in clean_image for c in " \t\n\r;|<>&`$()\"'\\"):
            raise ConfigError(f"Invalid container image '{image}': Image name contains invalid characters.")
        if not cls.IMAGE_NAME_PATTERN.match(clean_image):
            raise ConfigError(f"Invalid container image '{image}': Must be a valid OCI container image reference.")
        return clean_image

    @staticmethod
    def wrap_command(
        cmd: List[str],
        net: bool = False,
        compiling: bool = False,
        sandbox_cfg: Optional[Dict[str, Any]] = None,
        custom_env: Optional[Dict[str, str]] = None,
    ) -> List[str]:
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

        user_opt = sandbox_cfg.get("user")
        if user_opt:
            container_cmd.extend(["--user", str(user_opt)])
        elif hasattr(os, "getuid") and hasattr(os, "getgid"):
            container_cmd.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
        
        if not net:
            container_cmd.extend(["--network", "none"])

        container_name = sandbox_cfg.get("container_name") or f"run-sandbox-{uuid.uuid4().hex[:12]}"
        container_cmd.extend(["--name", container_name])

        if custom_env:
            for k, v in custom_env.items():
                container_cmd.extend(["-e", f"{k}={v}"])
            
        if sandbox_cfg.get("dockerfile"):
            base_image = ContainerSandbox._build_dockerfile(sandbox_cfg["dockerfile"], engine)
        elif sandbox_cfg.get("image"):
            base_image = sandbox_cfg["image"]
        else:
            base_image = ContainerSandbox.get_heuristic_image(cmd)
                
        base_image = ContainerSandbox.validate_image_name(base_image)
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
    def wrap_command(
        cmd: List[str],
        compose_file: str,
        service: str,
        workdir: Optional[str] = None,
        custom_env: Optional[Dict[str, str]] = None,
        sandbox_cfg: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        sandbox_cfg = sandbox_cfg or {}
        exec_cmd = ["docker", "compose", "-f", compose_file, "exec", "-T"]
        
        user_opt = sandbox_cfg.get("user")
        if user_opt:
            exec_cmd.extend(["--user", str(user_opt)])
        elif hasattr(os, "getuid") and hasattr(os, "getgid"):
            exec_cmd.extend(["--user", f"{os.getuid()}:{os.getgid()}"])

        if custom_env:
            for k, v in custom_env.items():
                exec_cmd.extend(["-e", f"{k}={v}"])

        target_workdir = workdir or sandbox_cfg.get("compose_workdir")
        if target_workdir:
            exec_cmd.extend(["-w", str(target_workdir)])

        exec_cmd.append(service)
        exec_cmd.extend(cmd)
        return exec_cmd


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
        image = ContainerSandbox.validate_image_name(image)
        actual_cwd = cwd or os.getcwd()
        Printer.info(f"Starting persistent sandbox container ({image})...")
        cmd = [
            engine, "run", "-d", "--rm",
            "--security-opt=no-new-privileges",
            "--cap-drop=ALL",
            "-v", f"{actual_cwd}:{actual_cwd}:rw",
            "-w", actual_cwd
        ]
        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            cmd.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
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
    def wrap_command(cls, cmd: List[str], custom_env: Optional[Dict[str, str]] = None) -> List[str]:
        if not cls._container_id:
            raise ExecutionError("Persistent container is not running.")
        exec_cmd = [cls._engine, "exec"]
        if custom_env:
            for k, v in custom_env.items():
                exec_cmd.extend(["-e", f"{k}={v}"])
        exec_cmd.extend(["-w", os.getcwd(), cls._container_id] + cmd)
        return exec_cmd
