from pathlib import Path
from typing import List, Optional, Dict, Any
import subprocess as spc
import re
import concurrent.futures
import os
from util.output import Printer, Colors
from util.errors import ConfigError, ExecutionError
from util.cache import CacheManager
from .base_runner import BaseRunner
from .rust_handler import RustHandler

class CompilerRunner(BaseRunner, RustHandler):
    """
    Main runner class that handles compilation and execution logic for various languages.
    Inherits from BaseRunner and RustHandler.
    """
    def __init__(self, op_flags: Dict[str, Any], extra_flags: str = ""):
        """
        Initialize the CompilerRunner.

        Args:
            op_flags (Dict[str, Any]): Operation flags.
            extra_flags (str): Extra compiler flags.
        """
        super().__init__(op_flags, extra_flags)
        self.c_family_ext = {'.c', '.cpp', '.cc'}
        self.c_family_header_ext = {'.h', '.hpp'}
        self.java_ext = {'.java'}
        self.cache = CacheManager()

    def find_source_files(self, path: Path, max_depth: Optional[int] = None) -> List[str]:
        """
        Recursively find C/C++/Java source files with optional max depth.

        Args:
            path (Path): Starting directory.
            max_depth (Optional[int]): Maximum depth to recurse. None for infinite.

        Returns:
            List[str]: List of found source file paths.
        """
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
        """
        Compile and run the provided files.

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

    def _get_python_executable(self) -> str:
        """
        Check for .venv or .env and return python path, else system default.

        Returns:
            str: Path to the python executable or command name.
        """
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
        """
        Handle execution flow for a single file.

        Args:
            fp (Path): Path to the source file.
        """
    def _handle_single_file(self, fp: Path):
        """
        Handle execution flow for a single file.

        Args:
            fp (File Path): Path to the source file.
        """
        ext = fp.suffix.lower()
        
        # Auto-detect language by shebang if no extension
        if not ext and fp.is_file():
            try:
                with open(fp, 'r') as f:
                    first_line = f.readline().strip()
                    if first_line.startswith("#!"):
                        if "python" in first_line:
                            ext = ".py"
                        elif "bash" in first_line or "sh" in first_line:
                            # We don't have sh runner explicit, but...
                            # Maybe we can support shell scripts via generic runner?
                            # For now just detecting python is a good start.
                            pass
                        elif "ruby" in first_line:
                             # If we had ruby support...
                             pass
            except Exception:
                pass

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
                
                self.run_command(cmd, compiling=True)
                # If raises exception, we won't reach here
                
                # Extract main class and run
                main_class = self._extract_java_main_class(fp)
                if main_class:
                    class_file = fp.with_suffix('.class')
                    self.output_files.append(class_file)
                    self.run_command(["java", main_class])
                else:
                    raise ExecutionError(f"Could not find main class in {fp}")
                        
            case _ if ext in self.c_family_ext:
                lang = "c" if ext == ".c" else "cpp"
                default_compiler = "gcc" if lang == "c" else "g++"
                compiler = self.config.get_runner(lang, default_compiler)
                
                preset_flags = self.config.get_preset_flags(self.preset, lang)

                cmd = [compiler] + self.extra_flags + preset_flags + [str(fp), "-o", str(out_name)]
                
                self.run_command(cmd, compiling=True)
                self.output_files.append(out_name)
                self._execute_binary(out_name)
            case _:
                # Check for custom language configuration
                lang_config = self.config.get_language_by_extension(ext)
                if lang_config:
                    self._handle_custom_language(fp, lang_config, out_name)
                else:
                    raise ConfigError(f"Unsupported extension: {ext}")

    def _handle_multi_compile(self, paths: List[Path]):
        """
        Handle multi-file compilation by detecting language type.

        Args:
            paths (List[Path]): List of all source files.
        """
        # Detect language type from files
        c_sources = [p for p in paths if p.suffix in self.c_family_ext]
        java_sources = [p for p in paths if p.suffix in self.java_ext]
        
        if c_sources:
            self._handle_multi_c_family(c_sources, paths)
        elif java_sources:
            self._handle_multi_java(java_sources)
        else:
            raise ConfigError("No supported files found for multi-compile")

    def _compile_object_file(self, compiler: str, source: Path, extra_cmd: List[str]) -> Optional[Path]:
        """
        Compile a single source file to object file.
        Returns path to object file if successful, None otherwise.
        """
        # Use cache directory for object files
        obj_file = self.cache.get_object_path(source)
        if self.is_posix:
             # Ensure extension is correct for platform if needed, though suffix check handles it
             pass 
        else:
             obj_file = obj_file.with_suffix(".obj")

        # Check cache
        if not self.cache.is_changed(source) and obj_file.exists():
            # Cache hit
            return obj_file

        Printer.action("COMPILE", f"{source.name} -> object")
        cmd = [compiler, "-c", str(source), "-o", str(obj_file)] + extra_cmd
        
        try:
            self.run_command(cmd, compiling=True)
            # We DONT add to output_files because we want to persist them in cache
            # self.output_files.append(obj_file) 
            self.cache.update_cache(source)
            return obj_file
        except ExecutionError:
            return None

    def _handle_multi_c_family(self, sources: List[Path], all_paths: List[Path]):
        """
        Handle multi-file C/C++ compilation with parallel execution and caching.

        Args:
            sources (List[Path]): List of source files.
            all_paths (List[Path]): List of all files including headers.
        """
        headers = [p for p in all_paths if p.suffix in self.c_family_header_ext]
        
        main_source = sources[0]
        ext = main_source.suffix.lower()
        lang = "c" if ext == ".c" else "cpp"
        default_compiler = "gcc" if lang == "c" else "g++"
        compiler = self.config.get_runner(lang, default_compiler)
        
        preset_flags = self.config.get_preset_flags(self.preset, lang)
        
        # Base command for object file compilation
        base_cmd = self.extra_flags + preset_flags
        include_dirs = {str(h.parent) for h in headers}
        for d in include_dirs:
            base_cmd.append(f"-I{d}")

        object_files = []
        failed = False
        
        # Parallel compilation
        max_workers = min(32, (os.cpu_count() or 1) + 4)
        Printer.info(f"Compiling {len(sources)} files using {max_workers} threads...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_source = {
                executor.submit(self._compile_object_file, compiler, src, base_cmd): src 
                for src in sources
            }
            
            for future in concurrent.futures.as_completed(future_to_source):
                src = future_to_source[future]
                try:
                    obj_path = future.result()
                    if obj_path:
                        object_files.append(obj_path)
                    else:
                        failed = True
                except Exception as e:
                    Printer.error(f"Failed to compile {src}: {e}")
                    failed = True

        if failed:
            raise ExecutionError("Build failed during compilation phase.")

        # Link
        out_name = self.get_executable_path(main_source)
        link_cmd = [compiler] + [str(o) for o in object_files] + ["-o", str(out_name)] + self.extra_flags + preset_flags
        
        if self.run_command(link_cmd, compiling=True):
            self.output_files.append(out_name)
            self._execute_binary(out_name)

    def _handle_multi_java(self, sources: List[Path]):
        """
        Handle multi-file Java compilation.

        Args:
            sources (List[Path]): List of Java source files.
        """
        compiler = self.config.get_runner("java", "javac")
        preset_flags = self.config.get_preset_flags(self.preset, "java")
        
        # Compile all Java files
        cmd = [compiler] + self.extra_flags + preset_flags + [str(s) for s in sources]
        
        self.run_command(cmd, compiling=True)
        
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
             raise ExecutionError(f"Could not find main class in {sources[0]}")

    def _extract_java_main_class(self, java_file: Path) -> Optional[str]:
        """
        Extract the main class name from a Java file.

        Args:
            java_file (Path): Path to the Java file.

        Returns:
            Optional[str]: Name of the main class, or None if not found.
        """
        try:
            with open(java_file, 'r') as f:
                content = f.read()
                
                # Check for package declaration
                package_name = ""
                package_match = re.search(r'^\s*package\s+([\w.]+)\s*;', content, re.MULTILINE)
                if package_match:
                    package_name = package_match.group(1) + "."

                # Look for public class declaration
                
                # Match: public class ClassName
                match = re.search(r'public\s+class\s+(\w+)', content)
                if match:
                    return package_name + match.group(1)
                # Fallback: try to find any class with main method
                match = re.search(r'class\s+(\w+)\s*\{[^}]*public\s+static\s+void\s+main', content, re.DOTALL)
                if match:
                    return package_name + match.group(1)
        except Exception as e:
            # This is a bit lower level error, maybe just logging is fine, but lets conform
             raise ExecutionError(f"Error reading Java file: {e}")
        return None

    def _handle_custom_language(self, fp: Path, lang_config: dict, out_name: Path):
        """
        Handle custom language execution based on configuration.

        Args:
            fp (Path): Source file path.
            lang_config (dict): Language configuration dictionary.
            out_name (Path): Output executable path.
        """
        lang_name = lang_config.get("name", "unknown")
        runner = lang_config.get("runner")
        lang_type = lang_config.get("type", "interpreter")
        
        if not runner:
             raise ConfigError(f"No runner specified for language: {lang_name}")
        
        if lang_type == "interpreter":
            # Run directly like Python, Ruby, etc.
            flags = lang_config.get("compile_flags", []) # List
            preset_flags = self.config.get_preset_flags(self.preset, lang_name)

            cmd = [runner] + flags + self.extra_flags + preset_flags + [str(fp)]

            self.run_command(cmd)
        elif lang_type == "compiler":
            # Compile first, then execute like C/C++
            compile_flags = lang_config.get("compile_flags", [])
            preset_flags = self.config.get_preset_flags(self.preset, lang_name)
            
            cmd = [runner] + compile_flags + self.extra_flags + preset_flags + [str(fp), "-o", str(out_name)]
            
            self.run_command(cmd, compiling=True)
            self.output_files.append(out_name)
            self._execute_binary(out_name)
        else:
             raise ConfigError(f"Unknown language type '{lang_type}' for {lang_name}")

    def _execute_binary(self, bin_path: Path):
        """
        Execute a compiled binary.

        Args:
            bin_path (Path): Path to the binary.
        """
        target = str(bin_path) if self.is_posix else str(bin_path.absolute())
        # Ensure ./ for POSIX relative paths
        if self.is_posix and not target.startswith('/') and not target.startswith('./'):
             target = f"./{target}"
        self.run_command([target])