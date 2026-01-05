#!/usr/bin/env python3

import os
import subprocess as spc
import argparse
import sys
import shlex
import time
from pathlib import Path
from typing import List, Optional

class Colors:
    GREEN = '\033[92m'
    CYAN = '\033[96m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    GRAY = '\033[1;30m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

class Printer:
    @staticmethod
    def action(tag: str, message: str, color: str = Colors.GREEN):
        # [ TAG ] Message
        print(f"{Colors.BOLD}{color}[ {tag} ]{Colors.RESET} {message}")

    @staticmethod
    def time(seconds: float):
         print(f"{Colors.GRAY}  -> Took {seconds:.3f}s{Colors.RESET}")

    @staticmethod
    def error(message: str):
        print(f"{Colors.BOLD}{Colors.RED}[ ERROR ]{Colors.RESET} {message}")
    
    @staticmethod
    def info(message: str):
         print(f"{Colors.BOLD}{Colors.CYAN}[ INFO ]{Colors.RESET} {message}")

    @staticmethod
    def warning(message: str):
         print(f"{Colors.BOLD}{Colors.YELLOW}[ WARN ]{Colors.RESET} {message}")

    @staticmethod
    def separator():
        print(f"\n{Colors.GRAY}{'-'*30}{Colors.RESET}\n")

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
        self.extra_flags = shlex.split(clean_flags) if clean_flags else []

    def get_executable_path(self, source_path: Path) -> Path:
        name = source_path.stem
        # Windows: .exe, POSIX: .out
        return Path(f"{name}.exe" if not self.is_posix else f"./{name}.out")

    def run_command(self, cmd: List[str], use_shell: bool = False, compiling: bool = False) -> bool:
        try:
            tag = "COMPILE" if compiling else "RUN"
            cmd_str = " ".join(cmd)
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
            cmd = ["rustc", str(fp), "-o", str(out_name)] + self.extra_flags
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
                compiler = "gcc" if ext == ".c" else "g++"
                cmd = [compiler] + self.extra_flags + [str(fp), "-o", str(out_name)]
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
            compiler = "gcc" if ext == ".c" else "g++"
            out_name = self.get_executable_path(main_source)

            cmd = [compiler] + self.extra_flags + [str(s) for s in sources]
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
                if f.exists():
                    try:
                        f.unlink()
                    except OSError:
                        pass

def main():
    parser = argparse.ArgumentParser(description="Professional Auto Compiler & Runner")
    parser.add_argument("files", nargs="*", help="Files to compile and run")
    
    parser.add_argument("--keep", action="store_true", help="Keep the output binary(s)")
    parser.add_argument("-m", "--multi", action="store_true", help="Compile multi-files")
    parser.add_argument("-t", "--time", action="store_true", help="Time counter for execute binary")
    
    parser.add_argument("-L", "--link-auto", nargs="?", const=-1, type=int, help="Auto find and link C/C++ files. Optional depth arg (default: infinite)")
    parser.add_argument("-f", "--flags", type=str, default="", help='Compiler flags')
 
    # Process -f-flags without space
    processed_args = []
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg.startswith("-f") and len(arg) > 2:
            processed_args.append("-f")
            processed_args.append(arg[2:])
        else:
            processed_args.append(arg)
        i += 1

    args = parser.parse_args(processed_args)

    # Process operation and flag(s) -> dictionary of it
    operator_flags = {
        "multi" : args.multi,
        "keep" : args.keep,
        "time" : args.time
    }

    # Init runner
    runner = CompilerRunner(op_flags=operator_flags, extra_flags=args.flags)

    # 1. Check if files provided
    if args.files:
        try:
            runner.compile_and_run(args.files, args.multi)
        finally:
            runner.cleanup()
        return 0

    # 2. Check for -L auto-link mode
    # If -L is present, args.link_auto will be either -1 (if no value provided) or the int value
    if args.link_auto is not None:
        depth = args.link_auto if args.link_auto != -1 else None
        src_files = runner.find_source_files(Path("."), max_depth=depth)
        if not src_files:
            Printer.error(f"No C/C++ source files found via -L auto-search (depth={depth}).")
            return 1
        Printer.info(f"Auto-found {len(src_files)} source files: {src_files}")
        try:
            runner.compile_and_run(src_files, multi=True)
        finally:
            runner.cleanup()
        return 0

    # 3. No files provided -> Check for implicit Cargo Project
    if Path("Cargo.toml").exists():
        # Auto-detect cargo project
        runner.run_cargo_mode(Path("Cargo.toml"))
        return 0

    # 4. No files, No Cargo -> Fallback to Input
    try:
        print(f"{Colors.YELLOW}[ INPUT ] No file given, enter file(s) name: {Colors.RESET}", end="")
        val = input().strip()
        if val: 
            args.files = shlex.split(val)
            runner.compile_and_run(args.files, args.multi)
    except (EOFError, KeyboardInterrupt):
        return 1
    finally:
        runner.cleanup()
        
    return 0

if __name__ == "__main__":
    exit_code = main()
    Printer.separator()
    sys.exit(exit_code)