from pathlib import Path
from typing import List, Optional, Dict, Any
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
from .handler_interface import ExecutionContext
from .registry import HandlerRegistry

class CompilerRunner(BaseRunner, RustHandler, PythonHandler, JavaHandler, 
                     CFamilyHandler, ScriptHandler, CustomLanguageHandler):
    """
    Main runner class that handles compilation and execution logic for various languages.
    Inherits from BaseRunner and language-specific handlers.
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
        self.c_family_ext = {'.c', '.cpp', '.cc'}
        self.c_family_header_ext = {'.h', '.hpp'}
        self.java_ext = {'.java'}
        
        if self.flags.get("no_cache", False):
            self.cache = None
            Printer.debug("Cache disabled via --no-cache")
        else:
            self.cache = CacheManager()

        self.registry = HandlerRegistry(custom_handler=self)

    def _get_context(self) -> ExecutionContext:
        """Create current execution context."""
        return ExecutionContext(
            flags=self.flags,
            extra_flags=self.extra_flags,
            run_args=self.run_args,
            preset=self.preset,
            config=self.config,
            cache=self.cache,
            output_files=self.output_files,
            is_posix=self.is_posix,
            runner_ref=self
        )

    def find_source_files(self, path: Path, max_depth: Optional[int] = None) -> List[str]:
        """
        Recursively find C/C++/Java source files with optional max depth.
        Ignores hidden folders and common build/cache directories.

        Args:
            path (Path): Starting directory.
            max_depth (Optional[int]): Maximum depth to recurse. None for infinite.

        Returns:
            List[str]: List of found source file paths.
        """
        files = []
        ext = self.c_family_ext.union(self.java_ext)
        ignore_dirs = {'.git', '.venv', 'venv', 'env', 'node_modules', '.run_cache', 'build', 'target', '__pycache__'}
        start_level = len(path.absolute().parts)
        
        for root, dirs, filenames in os.walk(path):
            dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith('.')]
            
            current_level = len(Path(root).absolute().parts)
            if max_depth is not None and (current_level - start_level > max_depth):
                dirs[:] = []
                continue
                
            for filename in filenames:
                if filename in self.exclude_files:
                    continue
                    
                p = Path(root) / filename
                if p.suffix in ext and p.suffix not in self.exclude_exts:
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
            name = fp.name

            if name in self.exclude_files:
                if not self.flags.get("quiet", False):
                    Printer.action("SKIP", f"{name} is in exclude files", Colors.GRAY)
                return

            ext = fp.suffix.lower()
            if not ext and fp.is_file():
                ext = self._detect_language_from_shebang(fp)
            
            if ext in self.exclude_exts:
                if not self.flags.get("quiet", False):
                    Printer.action("SKIP", f"{ext} file is exclude extensions", Colors.GRAY)
                return

            out_name = self.get_executable_path(fp)

            lang_config = self.config.get_language_by_extension(ext)
            if lang_config:
                self._handle_custom_language(fp, lang_config, out_name)
            else:
                match ext:
                    case ".py":
                        self._handle_python_execution(fp)
                    case ".sh":
                        self._handle_bash_execution(fp)
                    case ".rb":
                        self._handle_ruby_execution(fp)
                    case ".js":
                        self._handle_node_execution(fp)
                    case ".pl":
                        self._handle_perl_execution(fp)
                    case ".lua":
                        self._handle_lua_execution(fp)
                    case ".rs":
                        self._handle_rust_execution(fp)
                    case ".java":
                        self._handle_java_single_file(fp)
                    case _ if ext in self.c_family_ext:
                        self._handle_c_family_single_file(fp)
                    case _:
                        raise ConfigError(f"Unsupported extension: {ext}")
            return True
                        
        except (ConfigError, ExecutionError, FileNotFoundError, OSError) as e:
            Printer.error(f"Failed to process {fp}: {e}")
            return False
        except Exception as e:
            Printer.error(f"Unexpected error processing {fp}: {e}")
            return False

    def _handle_multi_compile(self, paths: List[Path]):
        """
        Handle multi-file compilation by detecting language type.
        Custom languages from config take highest priority.

        Args:
            paths (List[Path]): List of all source files.
        """
        if not paths:
            return

        # 1. Custom language check
        first_ext = paths[0].suffix.lower()
        lang_config = self.config.get_language_by_extension(first_ext)
        if lang_config:
            out_name = self.get_executable_path(paths[0])
            self._execute_custom_multi(paths, lang_config, out_name, self._get_context())
            return

        # 2. Built-in multi-file check (C/C++ or Java)
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
            args (List[str]): List of arguments.
        """
        if self.flags.get("build_only"):
            Printer.action("BUILD", f"Binary generated successfully: {bin_path}", Colors.GREEN)
            return

        target = str(bin_path) if self.is_posix else str(bin_path.absolute())

        if self.is_posix and not target.startswith('/') and not target.startswith('./'):
            target = f"./{target}"
        
        cmd = [target] + args + self.run_args
        self.run_command(cmd)
