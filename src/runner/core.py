from pathlib import Path
from typing import List, Optional, Dict, Any
import re
import concurrent.futures
import os
from util.output import Printer, Colors
from util.errors import ConfigError, ExecutionError
from util.cache import CacheManager
from .base_runner import BaseRunner
from .rust_handler import RustHandler
from .python_handler import PythonHandler
from .java_handler import JavaHandler
from .c_family_handler import CFamilyHandler
from .script_handler import ScriptHandler
from .custom_language_handler import CustomLanguageHandler

class CompilerRunner(BaseRunner, RustHandler, PythonHandler, JavaHandler, 
                     CFamilyHandler, ScriptHandler, CustomLanguageHandler):
    """
    Main runner class that handles compilation and execution logic for various languages.
    Inherits from BaseRunner and all language-specific handlers.
    """
    def __init__(self, op_flags: Dict[str, Any], extra_flags: str = "", run_args: str = ""):
        """
        Initialize the CompilerRunner.

        Args:
            op_flags (Dict[str, Any]): Operation flags.
            extra_flags (str): Extra compiler flags.
            run_args (str): Arguments to pass to the executed program.
        """
        super().__init__(op_flags, extra_flags, run_args)
        # Initialize CFamilyHandler attributes
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

    def _handle_single_file(self, fp: Path):
        """
        Handle execution flow for a single file.
        Continues execution even if errors occur.

        Args:
            fp (Path): Path to the source file.
        """
        try:
            ext = fp.suffix.lower()
            
            # Auto-detect language by shebang if no extension
            if not ext and fp.is_file():
                ext = self._detect_language_from_shebang(fp)

            out_name = self.get_executable_path(fp)

            match ext:
                case ".py":
                    self._handle_python_execution(fp)
                case ".lua":
                    self._handle_lua_execution(fp)
                case ".rs":
                    self._handle_rust_execution(fp)
                case ".java":
                    self._handle_java_single_file(fp)
                case _ if ext in self.c_family_ext:
                    self._handle_c_family_single_file(fp)
                case _:
                    # Check for custom language configuration
                    lang_config = self.config.get_language_by_extension(ext)
                    if lang_config:
                        self._handle_custom_language(fp, lang_config, out_name)
                    else:
                        raise ConfigError(f"Unsupported extension: {ext}")
                        
        except (ConfigError, ExecutionError, FileNotFoundError, OSError) as e:
            # Log the error but continue processing other files
            Printer.error(f"Failed to process {fp}: {e}")
        except Exception as e:
            # Catch any other unexpected errors and continue
            Printer.error(f"Unexpected error processing {fp}: {e}")

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

    def _execute_binary(self, bin_path: Path, args: List[str] = []):
        """
        Execute a compiled binary.

        Args:
            bin_path (Path): Path to the binary.
            args (List): List of arguments.
        """
        target = str(bin_path) if self.is_posix else str(bin_path.absolute())

        # Ensure ./ for POSIX relative paths
        if self.is_posix and not target.startswith('/') and not target.startswith('./'):
             target = f"./{target}"
        
        cmd = [target] + args + self.run_args
        self.run_command(cmd)
