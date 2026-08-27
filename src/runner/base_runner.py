import os
import subprocess as spc
import time
import shlex
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

        # Check for suspicious flags (if needed, but might be annoying for compilers)
        # SecurityManager.check_suspicious_flags(cmd)

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
            try:
                stdin_file = open(stdin_path, "r")
            except Exception as e:
                Printer.error(f"Failed to open stdin file {stdin_path}: {e}")

        # Setup quiet mode for compiler
        stdout_dest = spc.DEVNULL if self.flags.get("quiet", False) and compiling else None
        stderr_dest = spc.DEVNULL if self.flags.get("quiet", False) and compiling else None

        timeout = self.flags.get("timeout") if not compiling else None

        target_cmd = cmd_str if use_shell and isinstance(cmd, list) else cmd

        try:
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
            
            if stdin_file:
                stdin_file.close()
                
            if self.flags.get("time", False) and not compiling:
                Printer.time(time.perf_counter() - start_time)
            
            if result.returncode != 0:
                if compiling:
                    raise CompilationError(f"Compilation failed with exit code {result.returncode}")
                else:
                    raise ExecutionError(f"Execution failed with exit code {result.returncode}")
            return True
            
        except spc.TimeoutExpired:
            if stdin_file:
                stdin_file.close()
            raise ExecutionError(f"Execution timed out after {timeout} seconds.")
        except FileNotFoundError:
            if stdin_file:
                stdin_file.close()
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
                
                if f.exists():
                    try:
                        f.unlink()
                    except OSError as e:
                        Printer.warning(f"Failed to cleanup {f}: {e}")
