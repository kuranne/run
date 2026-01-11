import os
import subprocess as spc
import time
import shlex
from typing import List, Dict, Optional, Any
from pathlib import Path
from util.config import Config
from util.output import Printer, Colors

class BaseRunner:
    """
    Base class for runners, handling common functionality like command execution,
    platform detection, and cleanup.
    """
    def __init__(self, op_flags: Dict[str, Any], extra_flags: str = ""):
        """
        Initialize the BaseRunner.

        Args:
            op_flags (Dict[str, Any]): Dictionary of operation flags (e.g., 'dry_run', 'preset').
            extra_flags (str): String of extra compiler flags.
        """
        # Platform detection
        self.is_posix = os.name == "posix"

        # Argrument
        self.flags = op_flags
        self.dry_run = self.flags.get("dry_run", False)
        self.preset = self.flags.get("preset", None)
        
        # Config & Others
        self.config = Config()
        self.output_files: List[Path] = []

        # Clean flags from extra quotes and split into list
        clean_flags = extra_flags.strip().strip('"').strip("'")
        self.extra_flags = shlex.split(clean_flags) if clean_flags else []

    def get_executable_path(self, source_path: Path) -> Path:
        """
        Determine the executable path based on the source file and platform.

        Args:
            source_path (Path): Path to the source file.

        Returns:
            Path: Path to the expected executable file.
        """
        name = source_path.stem
        # Windows: .exe, POSIX: .out
        return Path(f"{name}.exe" if not self.is_posix else f"./{name}.out")

    def run_command(self, cmd: List[str], use_shell: bool = False, compiling: bool = False) -> bool:
        """
        Execute a shell command.

        Args:
            cmd (List[str]): Command components as a list.
            use_shell (bool): Whether to use shell execution.
            compiling (bool): True if this is a compilation step (affects output tag).

        Returns:
            bool: True if command executed successfully (exit code 0), False otherwise.
        """
        try:
            tag = "COMPILE" if compiling else "RUN"
            cmd_str = " ".join(cmd)
            
            if self.dry_run:
                Printer.action("DRY-RUN", f"{tag}: {cmd_str}", Colors.YELLOW)
                return True

            Printer.action(tag, cmd_str)

            start_time = time.perf_counter()
            result = spc.run(cmd, check=False, shell=use_shell)
            
            if self.flags["time"]:
                Printer.time(time.perf_counter() - start_time)
            return result.returncode == 0
        except FileNotFoundError:
            Printer.error(f"Command '{cmd[0]}' not found.")
            return False
        
    def _compile_c_family(self, fp: Path):
        """
        Handles C/C++ compilation and execution (Single file).
        
        Args:
            fp (Path): Path to the source file.
        """
        lang = "c" if fp.suffix == ".c" else "cpp"
        compiler = self.config.get_runner(lang, "gcc" if lang == "c" else "g++")
        out_name = self.get_executable_path(fp)
        
        preset_flags = self.config.get_preset_flags(self.preset, lang)
        cmd = [compiler] + self.extra_flags + preset_flags + [str(fp), "-o", str(out_name)]
        
        if self.run_command(cmd, compiling=True):
            self.output_files.append(out_name)
            self._execute_binary(out_name)

    def compile_and_run(self, files: List[str], multi: bool = False):
        """
        Main entry point to compile and run files.

        Args:
            files (List[str]): List of file paths to process.
            multi (bool): Whether to treat files as a single multi-file project.
        """
        if not files: return
        file_paths = [Path(f) for f in files]
        
        Printer.separator()
        if multi:
            self._handle_multi_compile(file_paths)
        else:
            for fp in file_paths:
                self._handle_single_file(fp)

    def cleanup(self):
        """
        Clean up generated binary/class files if --keep is not specified.
        """
        if not self.flags["keep"]:
            for f in self.output_files:
                if self.dry_run:
                     Printer.action("DRY-RUN", f"Would delete: {f}", Colors.YELLOW)
                     continue
                
                if f.exists():
                    try:
                        f.unlink()
                    except OSError:
                        pass