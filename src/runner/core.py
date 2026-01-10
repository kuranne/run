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
        self.java_ext = {'.java'}

    def find_source_files(self, path: Path, max_depth: int = None) -> List[str]:
        """Recursively find c/c++/java source files with optional max depth"""
        files = []

        ext = self.c_family_ext.union(self.java_ext)
        
        # 0 means just the current directory (no recursion into subdirs)
        # 1 means current + 1 level deep
        
        start_level = len(path.absolute().parts)
        
        for p in path.rglob("*"):
            if max_depth is not None:
                current_level = len(p.parent.absolute().parts)
                if current_level - start_level > max_depth:
                    continue
                
            if p.is_file() and p.suffix in ext:
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
                compiler = self.config.get_runner("java", "javac")
                preset_flags = self.config.get_preset_flags(self.preset, "java")
                
                # Compile the Java file
                cmd = [compiler] + self.extra_flags + preset_flags + [str(fp)]
                if self.run_command(cmd, compiling=True):
                    # Extract main class and run
                    main_class = self._extract_java_main_class(fp)
                    if main_class:
                        class_file = fp.with_suffix('.class')
                        self.output_files.append(class_file)
                        self.run_command(["java", main_class])
                    else:
                        Printer.error(f"Could not find main class in {fp}")
                        
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
        # Detect language type from files
        c_sources = [p for p in paths if p.suffix in self.c_family_ext]
        java_sources = [p for p in paths if p.suffix in self.java_ext]
        
        if c_sources:
            self._handle_multi_c_family(c_sources, paths)
        elif java_sources:
            self._handle_multi_java(java_sources)
        else:
            Printer.error("No supported files found for multi-compile")

    def _handle_multi_c_family(self, sources: List[Path], all_paths: List[Path]):
        """Handle multi-file C/C++ compilation"""
        headers = [p for p in all_paths if p.suffix in self.c_family_header_ext]
        
        main_source = sources[0]
        ext = main_source.suffix.lower()
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

    def _handle_multi_java(self, sources: List[Path]):
        """Handle multi-file Java compilation"""
        compiler = self.config.get_runner("java", "javac")
        preset_flags = self.config.get_preset_flags(self.preset, "java")
        
        # Compile all Java files
        cmd = [compiler] + self.extra_flags + preset_flags + [str(s) for s in sources]
        
        if self.run_command(cmd, compiling=True):
            # Extract main class name from the first file
            main_class = self._extract_java_main_class(sources[0])
            if main_class:
                # Add .class files to cleanup
                for src in sources:
                    class_file = src.with_suffix('.class')
                    self.output_files.append(class_file)
                
                # Run the main class
                self.run_command(["java", main_class])
            else:
                Printer.error(f"Could not find main class in {sources[0]}")

    def _extract_java_main_class(self, java_file: Path) -> str:
        """Extract the main class name from a Java file"""
        try:
            with open(java_file, 'r') as f:
                content = f.read()
                # Look for public class declaration
                import re
                # Match: public class ClassName
                match = re.search(r'public\s+class\s+(\w+)', content)
                if match:
                    return match.group(1)
                # Fallback: try to find any class with main method
                match = re.search(r'class\s+(\w+)\s*\{[^}]*public\s+static\s+void\s+main', content, re.DOTALL)
                if match:
                    return match.group(1)
        except Exception as e:
            Printer.error(f"Error reading Java file: {e}")
        return None

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