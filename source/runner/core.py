import os
import subprocess as spc
import time
import shlex
from typing import List, Optional
from pathlib import Path
from runner.config import Config
from runner.ui import Printer, Colors

class CompilerRunner:
    def __init__(self, op_flags, extra_flags: str = ""):
        # Platform detection
        self.is_posix = os.name == "posix"
        
        # Initialize
        self.flags = op_flags
        self.output_files: List[Path] = []
        self.c_family_header_ext = {'.h', '.hpp'}
        self.c_family_ext = {'.c', '.cpp', '.cc'}

        # Clean flags extra '' and ""
        clean_flags = extra_flags.strip().strip('"').strip("'")
        self.extra_flags = shlex.spilt(clean_flags) if clean_flags else []
        
        
        self.dry_run = op_flags.get("dry_run", False)
        self.config = Config()
        self.preset = op_flags.get("preset", None)

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

    def find_source_files(self, path: Path, max_depth: int = None) -> List[str]:
        """Recursively find c/c++ source files with optional max depth"""
        files = []
        
        # 0 means just the current directory (no recursion into subdirs)
        # 1 means current + 1 level deep
        
        start_level = len(path.absolute().parts)
        
        for p in path.rglob("*"):
            if max_depth is not None:
                current_level = len(p.parent.absolute().parts)
                if current_level - start_level > max_depth:
                    continue
                
            if p.is_file() and p.suffix in self.c_family_ext:
                files.append(str(p))
        return files

    def compile_and_run(self, files: List[str], multi: bool = False):
        if not files: return
        file_paths = [Path(f) for f in files]
        
        Printer.separator()
        if multi:
            self._handle_multi_compile(file_paths)
        else:
            for fp in file_paths:
                self._handle_single_file(fp)

    # --- Cargo Utilities ---
    def _find_cargo_toml(self, start_path: Path) -> Optional[Path]:
        """Walk up to find Cargo.toml"""
        current = start_path.absolute()
        if current.is_file():
            current = current.parent
            
        for _ in range(3): # Check up to 3 levels up
            toml = current / "Cargo.toml"
            if toml.exists():
                return toml
            current = current.parent
        return None

    def _get_cargo_package_name(self, toml_path: Path) -> Optional[str]:
        """Simple parsing to get package name from Cargo.toml"""
        try:
            with open(toml_path, 'r', encoding='utf-8') as f:
                in_package = False
                for line in f:
                    line = line.strip()
                    if line == "[package]":
                        in_package = True
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        in_package = False
                    
                    if in_package and line.startswith("name"):
                        # name = "project_name"
                        parts = line.split('=')
                        if len(parts) >= 2:
                            return parts[1].strip().strip('"').strip("'")
        except Exception:
            pass
        return None

    def run_cargo_mode(self, toml_path: Path = None):
        """Handle cargo execution logic: run -q OR build && run"""
        # If path not provided, assume current dir
        if not toml_path:
            toml_path = Path("Cargo.toml")

        is_release = "--release" in self.extra_flags
        
        if is_release:
            # Case: Build Release -> Run Binary
            Printer.info("Building release...")
            build_cmd = ["cargo", "build"] + self.extra_flags
            if not self.run_command(build_cmd, compiling=True):
                return
            
            pkg_name = self._get_cargo_package_name(toml_path)
            if not pkg_name:
                Printer.error("Could not parse package name from Cargo.toml")
                return

            bin_name = f"{pkg_name}.exe" if not self.is_posix else pkg_name
            target_bin = toml_path.parent / "target" / "release" / bin_name
            
            if target_bin.exists():
                self._execute_binary(target_bin)
            else:
                Printer.error(f"Binary not found at {target_bin}")
        else:
            # Case: Default Run Quiet
            # Note: -q comes before --flags to ensure cargo itself is quiet
            cmd = ["cargo", "run", "-q"] + self.extra_flags
            self.run_command(cmd)

    def _handle_rust_execution(self, fp: Path):
        cargo_toml = self._find_cargo_toml(fp)
        
        if cargo_toml:
            Printer.info(f"Found Cargo project: {cargo_toml.parent.name}")
            self.run_cargo_mode(cargo_toml)
        else:
            # --- Rustc Mode (Single File) ---
            out_name = self.get_executable_path(fp)
            
            # Preset flags
            preset_flags = self.config.get_preset_flags(self.preset, "rust")
            rustc = self.config.get_runner("rust", "rustc")

            cmd = [rustc, str(fp), "-o", str(out_name)] + self.extra_flags + preset_flags
            if self.run_command(cmd, compiling=True):
                self.output_files.append(out_name)
                self._execute_binary(out_name)

    def _get_python_executable(self) -> str:
        """Check for .venv or .env and return python path, else system default"""
        potential_venvs = [".venv", ".env"]
        # Check in current working directory
        for venv in potential_venvs:
            venv_path = Path(venv)
            if venv_path.is_dir():
                if self.is_posix:
                    py_path = venv_path / "bin" / "python"
                else:
                    py_path = venv_path / "Scripts" / "python.exe"
                
                if py_path.exists():
                    Printer.info(f"Using venv: {venv}")
                    return str(py_path)
        
        return "python" if not self.is_posix else "python3"

    def _handle_single_file(self, fp: Path):
        ext = fp.suffix.lower()
        out_name = self.get_executable_path(fp)

        match ext:
            case ".py":
                prog = self._get_python_executable()
                self.run_command([prog, str(fp)])
            case ".lua":
                check_cmd = "where" if not self.is_posix else "command -v"
                is_lua = spc.run(f"{check_cmd} lua", shell=True, capture_output=True).returncode == 0
                prog = "lua" if is_lua else "luajit"
                self.run_command([prog, str(fp)])

            case ".java":
                self.run_command(["java", str(fp)])
            case ".js":
                self.run_command(["node", str(fp)])
            case ".go":
                self.run_command(["go", "run", str(fp)])
            case ".rs":
                self._handle_rust_execution(fp)

            case _ if ext in self.c_family_ext:
                lang = "c" if ext == ".c" else "cpp"
                default_compiler = "gcc" if lang == "c" else "g++"
                compiler = self.config.get_runner(lang, default_compiler)
                
                preset_flags = self.config.get_preset_flags(self.preset, lang)

                cmd = [compiler] + self.extra_flags + preset_flags + [str(fp), "-o", str(out_name)]
                if self.run_command(cmd, compiling=True):
                    self.output_files.append(out_name)
                    self._execute_binary(out_name)
            case _:
                Printer.error(f"Unsupported extension: {ext}")

    def _handle_multi_compile(self, paths: List[Path]):
        sources = [p for p in paths if p.suffix in self.c_family_ext]
        headers = [p for p in paths if p.suffix in self.c_family_header_ext]
        if not sources: return

        main_source = sources[0]
        ext = main_source.suffix.lower()

        if ext in self.c_family_ext:
            lang = "c" if ext == ".c" else "cpp"
            default_compiler = "gcc" if lang == "c" else "g++"
            compiler = self.config.get_runner(lang, default_compiler)
            
            preset_flags = self.config.get_preset_flags(self.preset, lang)
            out_name = self.get_executable_path(main_source)

            cmd = [compiler] + self.extra_flags + preset_flags + [str(s) for s in sources]
            include_dirs = {str(h.parent) for h in headers}
            for d in include_dirs:
                cmd.append(f"-I{d}")
            cmd += ["-o", str(out_name)]

            if self.run_command(cmd, compiling=True):
                self.output_files.append(out_name)
                self._execute_binary(out_name)
        else:
            Printer.error(f"Unsupported extension for multi: {ext}")

    def _execute_binary(self, bin_path: Path):
        target = str(bin_path) if self.is_posix else str(bin_path.absolute())
        # Ensure ./ for POSIX relative paths
        if self.is_posix and not target.startswith('/') and not target.startswith('./'):
             target = f"./{target}"
        self.run_command([target])

    def cleanup(self):
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
