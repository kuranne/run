import os
import sys
import subprocess as spc
import time
import shlex
import tempfile
from typing import List, Dict, Optional, Any
from pathlib import Path
from util.config import Config
from util.output import Printer, Colors
from util.errors import ExecutionError, CompilationError, ConfigError
from util.security import SecurityManager
from util.process import MonitoredPopen

class BaseRunner:
    """
    Base class for runners, handling common functionality like command execution,
    platform detection, and cleanup.
    """
    def __init__(self, op_flags: Dict[str, Any], extra_flags: str = "", run_args: str = ""):
        """
        Initialize BaseRunner.

        Args:
            op_flags (Dict[str, Any]): Dictionary of operation flags (e.g., 'dry_run', 'preset').
            extra_flags (str): String of extra compiler flags.
            run_args (str): Arguments to pass to the executed program.
        """
        # Platform detection
        self.is_posix = os.name == "posix"

        # Arguments
        self.flags = op_flags
        self.dry_run = self.flags.get("dry_run", False)
        self.preset = self.flags.get("preset", None)
        
        # Memory metrics tracking
        self.last_memory_bytes: Optional[int] = None
        self.last_compile_memory_bytes: Optional[int] = None
        
        # Config & Others
        self.config = Config()
        core_cfg = self.config.data.get("core", {})
        if not self.flags.get("sandbox"): self.flags["sandbox"] = core_cfg.get("sandbox", False)
        if not self.flags.get("sandbox_net"): self.flags["sandbox_net"] = core_cfg.get("sandbox_net", False)
        if not self.flags.get("restrict"): self.flags["restrict"] = core_cfg.get("restrict", False)
        
        excludes = self.config.get_exclude()
        self.output_files: List[Path] = []
        self.exclude_exts: List[str] = ['.toml', '.lock'] + excludes.get("extensions", [])
        self.exclude_files: List[str] = ['.git', '.gitignore'] + excludes.get("files", [])

        # Clean flags from extra quotes and split into list
        clean_flags = extra_flags.strip().strip('"').strip("'")
        self.extra_flags = shlex.split(clean_flags) if clean_flags else []
        
        # Run args
        clean_run_args = run_args.strip().strip('"').strip("'")
        self.run_args = shlex.split(clean_run_args) if clean_run_args else []

        # Inject sanitizer compiler flags
        if self.flags.get("asan"):
            self.extra_flags.extend(["-fsanitize=address,undefined", "-fno-omit-frame-pointer"])
        if self.flags.get("tsan"):
            self.extra_flags.append("-fsanitize=thread")
        if self.flags.get("sanitize"):
            self.extra_flags.append(f"-fsanitize={self.flags['sanitize']}")

        # Buffered stdin for pipe redirection (-i or -i -)
        self._buffered_stdin: Optional[str] = None
        if self.flags.get("stdin") == "-":
            try:
                if not sys.stdin.isatty():
                    self._buffered_stdin = sys.stdin.read()
            except Exception as e:
                Printer.warning(f"Failed to read from stdin: {e}")

    def get_executable_path(self, source_path: Path) -> Path:
        """
        Determine executable path based on source file and platform.

        Args:
            source_path (Path): Path to source file.

        Returns:
            Path: Path to expected executable file.
        """
        name = source_path.stem
        filename = f"{name}.exe" if not self.is_posix else f"{name}.out"
        
        out_dir = self.flags.get("out_dir")
        if out_dir:
            out_path = Path(out_dir)
            out_path.mkdir(parents=True, exist_ok=True)
            return out_path / filename
        
        return Path(f"./{filename}" if self.is_posix else filename)

    def run_command(self, cmd: List[str], use_shell: bool = False, compiling: bool = False) -> bool:
        """
        Execute a shell command.

        Args:
            cmd (List[str]): Command components as a list.
            use_shell (bool): Whether to use shell execution.
            compiling (bool): True if this is a compilation step (affects output tag).

        Returns:
            bool: True if command executed successfully (exit code 0).
            
        Raises:
            ExecutionError: If command fails / not found.
            CompilationError: If compilation command fails.
        """
        tag = "COMPILE" if compiling else "RUN"
        cmd_str = " ".join(cmd)
        
        if self.dry_run:
            Printer.action("DRY-RUN", f"{tag}: {cmd_str}", Colors.YELLOW)
            return True

        # Check for suspicious flags
        if not self.flags.get("unsafe", False) and not SecurityManager.check_suspicious_flags(cmd):
            if compiling:
                raise CompilationError(f"Rejected suspicious flag in compilation command: {' '.join(cmd)}")
            else:
                raise ExecutionError(f"Rejected suspicious flag in command: {' '.join(cmd)}")

        if not self.flags.get("quiet", False):
            Printer.action(tag, cmd_str)
        
        custom_env = {}
        for e in self.flags.get("env", []):
            if "=" in e:
                k, v = e.split("=", 1)
                custom_env[k] = v
        env = SecurityManager.sanitize_execution_env(
            custom_env=custom_env,
            strict_whitelist=bool(self.flags.get("sandbox") or self.flags.get("restrict"))
        )

        start_time = time.perf_counter()
        
        # Setup stdin
        stdin_file = None
        stdin_path = self.flags.get("stdin")
        if not compiling and stdin_path:
            if stdin_path == "-":
                if self._buffered_stdin is not None:
                    stdin_file = tempfile.TemporaryFile(mode="w+")
                    stdin_file.write(self._buffered_stdin)
                    stdin_file.seek(0)
            else:
                try:
                    stdin_file = open(stdin_path, "r")
                except Exception as e:
                    Printer.error(f"Failed to open stdin file {stdin_path}: {e}")
                    raise ExecutionError(f"Failed to open stdin file '{stdin_path}': {e}")

        # Setup quiet mode for compiler or output capture for expectation
        expect_path = self.flags.get("expect") if not compiling else None
        if expect_path:
            stdout_dest = spc.PIPE
        else:
            stdout_dest = spc.DEVNULL if self.flags.get("quiet", False) and compiling else None
        stderr_dest = spc.DEVNULL if self.flags.get("quiet", False) and compiling else None

        is_debug = bool(self.flags.get("debug") or self.flags.get("gdb") or self.flags.get("lldb"))
        timeout = self.flags.get("timeout") if (not compiling and not is_debug) else None

        if use_shell:
            target_cmd = shlex.join(cmd) if isinstance(cmd, list) else str(cmd)
        else:
            target_cmd = list(cmd) if isinstance(cmd, (list, tuple)) else shlex.split(str(cmd))

        sandbox_preexec_fn = None
        if self.flags.get("sandbox"):
            from util.sandbox import ContainerSandbox, PersistentSandbox, ComposeSandbox
            t_list = target_cmd if isinstance(target_cmd, list) else shlex.split(target_cmd)
            sandbox_cfg = self.config.get_sandbox_config() if hasattr(self, 'config') else {}
            
            if PersistentSandbox._container_id:
                t_list = PersistentSandbox.wrap_command(t_list)
            elif sandbox_cfg.get("compose"):
                svc = sandbox_cfg.get("compose_service", "app")
                t_list = ComposeSandbox.wrap_command(t_list, sandbox_cfg["compose"], svc)
            else:
                t_list = ContainerSandbox.wrap_command(
                    t_list, 
                    net=self.flags.get("sandbox_net", False), 
                    compiling=compiling, 
                    sandbox_cfg=sandbox_cfg
                )
            target_cmd = shlex.join(t_list) if use_shell else t_list
        elif not compiling and self.flags.get("restrict"):
            from util.sandbox import NativeRestrictor
            if sys.platform == "darwin":
                sandbox_preexec_fn = NativeRestrictor.macos_preexec_fn
            else:
                t_list = target_cmd if isinstance(target_cmd, list) else shlex.split(target_cmd)
                t_list = NativeRestrictor.wrap_command(t_list, net=self.flags.get("sandbox_net", False), compiling=compiling)
                target_cmd = shlex.join(t_list) if use_shell else t_list

        spc_kwargs = {
            "shell": use_shell,
            "env": env,
            "stdin": stdin_file,
            "stdout": stdout_dest,
            "stderr": stderr_dest
        }
        if sandbox_preexec_fn:
            spc_kwargs["preexec_fn"] = sandbox_preexec_fn

        mem_bytes = None
        captured_stdout = ""
        p = None
        try:
            try:
                track_mem = (not compiling) and self.flags.get("memory", False)
                popen_cls = MonitoredPopen if track_mem else spc.Popen
                p = popen_cls(target_cmd, **spc_kwargs)
                stdout_bytes, stderr_bytes = p.communicate(timeout=timeout)
                returncode = p.returncode
                if stdout_bytes is not None:
                    captured_stdout = stdout_bytes.decode("utf-8", errors="ignore")
                if track_mem and isinstance(p, MonitoredPopen):
                    mem_bytes = p.get_memory_bytes()
            finally:
                if stdin_file:
                    try:
                        stdin_file.close()
                    except Exception:
                        pass
                if not compiling:
                    self.last_memory_bytes = mem_bytes
                else:
                    self.last_compile_memory_bytes = mem_bytes
                
            if not compiling and not is_debug:
                if expect_path and captured_stdout and not self.flags.get("quiet", False):
                    print(captured_stdout, end="" if captured_stdout.endswith("\n") else "\n")

                elapsed = time.perf_counter() - start_time
                show_time = self.flags.get("time", False)
                show_mem = self.flags.get("memory", False)
                if show_time or show_mem:
                    Printer.metrics(
                        seconds=elapsed if show_time else None,
                        memory_bytes=mem_bytes if show_mem else None
                    )

                if expect_path:
                    try:
                        with open(expect_path, "r", encoding="utf-8", errors="ignore") as f:
                            expected_content = f.read()
                        
                        if expected_content.strip() == captured_stdout.strip():
                            Printer.action("PASS", f"Output matches {expect_path}", Colors.GREEN)
                        else:
                            Printer.action("FAIL", f"Output mismatch with {expect_path}", Colors.RED)
                            Printer.diff(expected_content, captured_stdout, expected_name=str(expect_path))
                            return False
                    except Exception as e:
                        Printer.error(f"Failed to read expectation file '{expect_path}': {e}")
                        return False
            
            if returncode != 0:
                if compiling:
                    raise CompilationError(f"Compilation failed with exit code {returncode}")
                else:
                    raise ExecutionError(f"Execution failed with exit code {returncode}")
            return True
            
        except spc.TimeoutExpired:
            if p is not None:
                try:
                    p.kill()
                    p.wait(timeout=5)
                    if track_mem and isinstance(p, MonitoredPopen):
                        mem_bytes = p.get_memory_bytes()
                        if not compiling:
                            self.last_memory_bytes = mem_bytes
                except Exception:
                    pass
            raise ExecutionError(f"Execution timed out after {timeout} seconds.")
        except FileNotFoundError:
            cmd_name = cmd[0] if isinstance(cmd, list) and cmd else str(cmd)
            raise ExecutionError(f"Command '{cmd_name}' not found.")
        
    def _compile_c_family(self, fp: Path):
        """
        Handles C/C++ compilation and execution (Single file).
        
        Args:
            fp (Path): Path to the source file.
        """
        lang = "c" if fp.suffix == ".c" else "cpp"
        
        # Override compiler if requested
        compiler_override = self.flags.get("compiler")
        if compiler_override:
            compiler = compiler_override
        else:
            compiler = self.config.get_runner(lang, "gcc" if lang == "c" else "g++")
            
        out_name = self.get_executable_path(fp)
        
        preset_flags = self.config.get_preset_flags(self.preset, lang)
        cmd = [compiler] + self.extra_flags + preset_flags + [str(fp), "-o", str(out_name)]
        
        # run_command raises exception on failure, so we don't need if check here anymore
        # but kept for flow clarity or if we catch it later
        if self.run_command(cmd, compiling=True):
            self.output_files.append(out_name)
            self._execute_binary(out_name)

    def compile_and_run(self, files: List[str], multi: bool = False):
        """
        Main entry point to compile and run files.
        Continues processing all files even if some encounter errors.

        Args:
            files (List[str]): List of file paths to process.
            multi (bool): Whether to treat files as a single multi-file project.
        """
        if not files: return
        file_paths = [Path(f) for f in files]
        
        if multi:
            try:
                self._handle_multi_compile(file_paths)
            except (ConfigError, ExecutionError, FileNotFoundError, OSError) as e:
                Printer.error(f"Multi-file compilation failed: {e}")
        else:
            for fp in file_paths:
                try:
                    self._handle_single_file(fp)
                except (ConfigError, ExecutionError, FileNotFoundError, OSError) as e:
                    # This should not happen since _handle_single_file catches its own errors,
                    # but as a fallback, catch any unexpected errors and continue
                    Printer.error(f"Unexpected error processing {fp}: {e}")

    def cleanup(self):
        """Clean up generated binary/class files if --keep is not specified."""
        if not self.flags.get("keep", False):
            for f in self.output_files:
                if self.dry_run:
                     Printer.action("DRY-RUN", f"Would delete: {f}", Colors.YELLOW)
                     continue
                
                try:
                    f.unlink(missing_ok=True)
                except OSError as e:
                    Printer.warning(f"Failed to cleanup {f}: {e}")
