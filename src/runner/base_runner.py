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
        
        # Config & Others
        self.config = Config()
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
        SecurityManager.check_suspicious_flags(cmd)

        if not self.flags.get("quiet", False):
            Printer.action(tag, cmd_str)
        
        env = SecurityManager.sanitize_execution_env()
        
        # Apply custom environment variables
        for e in self.flags.get("env", []):
            if "=" in e:
                k, v = e.split("=", 1)
                env[k] = v

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

        # Setup quiet mode for compiler or output capture for expectation
        expect_path = self.flags.get("expect") if not compiling else None
        if expect_path:
            stdout_dest = spc.PIPE
        else:
            stdout_dest = spc.DEVNULL if self.flags.get("quiet", False) and compiling else None
        stderr_dest = spc.DEVNULL if self.flags.get("quiet", False) and compiling else None

        is_debug = bool(self.flags.get("debug") or self.flags.get("gdb") or self.flags.get("lldb"))
        timeout = self.flags.get("timeout") if (not compiling and not is_debug) else None

        target_cmd = cmd_str if use_shell and isinstance(cmd, list) else cmd

        mem_bytes = None
        captured_stdout = ""
        p = None
        try:
            try:
                if not compiling and self.flags.get("memory", False):
                    if self.is_posix and timeout is None:
                        p = spc.Popen(
                            target_cmd,
                            shell=use_shell,
                            env=env,
                            stdin=stdin_file,
                            stdout=stdout_dest,
                            stderr=stderr_dest
                        )
                        _, status, rusage = os.wait4(p.pid, 0)
                        returncode = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else (status >> 8 if os.WIFEXITED(status) else 1)
                        if p.stdout:
                            captured_stdout = p.stdout.read().decode("utf-8", errors="ignore")
                        if sys.platform == "darwin":
                            mem_bytes = rusage.ru_maxrss
                        else:
                            mem_bytes = rusage.ru_maxrss * 1024
                    elif self.is_posix:
                        import resource
                        before_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
                        result = spc.run(
                            target_cmd,
                            check=False,
                            shell=use_shell,
                            env=env,
                            stdin=stdin_file,
                            stdout=stdout_dest,
                            stderr=stderr_dest,
                            timeout=timeout
                        )
                        returncode = result.returncode
                        if result.stdout:
                            captured_stdout = result.stdout.decode("utf-8", errors="ignore") if isinstance(result.stdout, bytes) else str(result.stdout)
                        after_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
                        rss_val = max(after_rss, before_rss)
                        mem_bytes = rss_val if sys.platform == "darwin" else rss_val * 1024
                    elif os.name == "nt":
                        import ctypes
                        from ctypes import wintypes
                        p = spc.Popen(
                            target_cmd,
                            shell=use_shell,
                            env=env,
                            stdin=stdin_file,
                            stdout=stdout_dest,
                            stderr=stderr_dest
                        )
                        returncode = p.wait(timeout=timeout)
                        if p.stdout:
                            captured_stdout = p.stdout.read().decode("utf-8", errors="ignore")
                        try:
                            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                                _fields_ = [
                                    ('cb', wintypes.DWORD),
                                    ('PageFaultCount', wintypes.DWORD),
                                    ('PeakWorkingSetSize', ctypes.c_size_t),
                                    ('WorkingSetSize', ctypes.c_size_t),
                                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                                    ('QuotaPagedPoolUsage', ctypes.c_size_t),
                                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                                    ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                                    ('PagefileUsage', ctypes.c_size_t),
                                    ('PeakPagefileUsage', ctypes.c_size_t),
                                ]
                            counters = PROCESS_MEMORY_COUNTERS()
                            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                            if ctypes.windll.psapi.GetProcessMemoryInfo(int(p._handle), ctypes.byref(counters), counters.cb):
                                mem_bytes = int(counters.PeakWorkingSetSize)
                        except Exception:
                            mem_bytes = None
                    else:
                        result = spc.run(
                            target_cmd,
                            check=False,
                            shell=use_shell,
                            env=env,
                            stdin=stdin_file,
                            stdout=stdout_dest,
                            stderr=stderr_dest,
                            timeout=timeout
                        )
                        returncode = result.returncode
                        if result.stdout:
                            captured_stdout = result.stdout.decode("utf-8", errors="ignore") if isinstance(result.stdout, bytes) else str(result.stdout)
                else:
                    result = spc.run(
                        target_cmd,
                        check=False,
                        shell=use_shell,
                        env=env,
                        stdin=stdin_file,
                        stdout=stdout_dest,
                        stderr=stderr_dest,
                        timeout=timeout
                    )
                    returncode = result.returncode
                    if result.stdout:
                        captured_stdout = result.stdout.decode("utf-8", errors="ignore") if isinstance(result.stdout, bytes) else str(result.stdout)
            finally:
                if stdin_file:
                    try:
                        stdin_file.close()
                    except Exception:
                        pass
                
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
