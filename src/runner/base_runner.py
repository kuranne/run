import os
import subprocess as spc
import time
import shlex
from typing import List
from pathlib import Path
from util.config import Config
from util.output import Printer, Colors

class BaseRunner:
    def __init__(self, op_flags, extra_flags: str = ""):
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
        name = source_path.stem
        # Windows: .exe, POSIX: .out
        return Path(f"{name}.exe" if not self.is_posix else f"./{name}.out")

    def run_command(self, cmd: List[str], use_shell: bool = False, compiling: bool = False) -> bool:
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
        """Handles C/C++ compilation and execution"""
        lang = "c" if fp.suffix == ".c" else "cpp"
        compiler = self.config.get_runner(lang, "gcc" if lang == "c" else "g++")
        out_name = self.get_executable_path(fp)
        
        preset_flags = self.config.get_preset_flags(self.preset, lang)
        cmd = [compiler] + self.extra_flags + preset_flags + [str(fp), "-o", str(out_name)]
        
        if self.run_command(cmd, compiling=True):
            self.output_files.append(out_name)
            self._execute_binary(out_name)

    def compile_and_run(self, files: List[str], multi: bool = False):
        if not files: return
        file_paths = [Path(f) for f in files]
        
        Printer.separator()
        if multi:
            self._handle_multi_compile(file_paths)
        else:
            for fp in file_paths:
                self._handle_single_file(fp)