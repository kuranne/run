from pathlib import Path
from typing import List
import subprocess as spc
from util.output import Printer, Colors
from .base_runner import BaseRunner
from .rust_handler import RustHandler

class CompilerRunner(BaseRunner, RustHandler):
    def __init__(self, op_flags, extra_flags: str = ""):
        super().__init__(op_flags, extra_flags)
        self.c_family_ext = {'.c', '.cpp', '.cc'}
        self.c_family_header_ext = {'.h', '.hpp'}

    def compile_and_run(self, files: List[str], multi: bool = False):
        if not files: return
        file_paths = [Path(f) for f in files]
        
        Printer.separator()
        if multi:
            self._handle_multi_compile(file_paths)
        else:
            for fp in file_paths:
                self._handle_single_file(fp)

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
            case ".rs":
                self._handle_rust_execution(fp)
            case ".java":
                self.run_command(["java", str(fp)])
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
                # Check for custom language configuration
                lang_config = self.config.get_language_by_extension(ext)
                if lang_config:
                    self._handle_custom_language(fp, lang_config, out_name)
                else:
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

    def _handle_custom_language(self, fp: Path, lang_config: dict, out_name: Path):
        """Handle custom language execution based on configuration"""
        lang_name = lang_config.get("name", "unknown")
        runner = lang_config.get("runner")
        lang_type = lang_config.get("type", "interpreter")
        
        if not runner:
            Printer.error(f"No runner specified for language: {lang_name}")
            return
        
        if lang_type == "interpreter":
            # Run directly like Python, Ruby, etc.
            self.run_command([runner, str(fp)])
        elif lang_type == "compiler":
            # Compile first, then execute like C/C++
            compile_flags = lang_config.get("compile_flags", [])
            preset_flags = self.config.get_preset_flags(self.preset, lang_name)
            
            cmd = [runner] + compile_flags + self.extra_flags + preset_flags + [str(fp), "-o", str(out_name)]
            if self.run_command(cmd, compiling=True):
                self.output_files.append(out_name)
                self._execute_binary(out_name)
        else:
            Printer.error(f"Unknown language type '{lang_type}' for {lang_name}")

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