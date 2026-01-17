from pathlib import Path
from typing import List, Optional
import concurrent.futures
import os

class CFamilyHandler:
    """
    Mixin class handling C/C++ specific operations.
    """
    def __init__(self):
        self.c_family_ext = {'.c', '.cpp', '.cc'}
        self.c_family_header_ext = {'.h', '.hpp'}

    def _compile_object_file(self, compiler: str, source: Path, extra_cmd: List[str], cache) -> Optional[Path]:
        """
        Compile a single source file to object file.
        Returns path to object file if successful, None otherwise.
        """
        if cache:
            # Use cache directory for object files
            obj_file = cache.get_object_path(source)
        else:
            # No cache: use source directory with different extension to avoid conflict
            # Or just use the standard object extension in a unique way?
            # Using .o in same dir is fine, but we must ensure it's cleaned up.
            # We prefix to avoid accidental overwrites of user files?
            # Let's keep it simple: source.o
            obj_file = source.with_suffix(".o") if self.is_posix else source.with_suffix(".obj")
            
        if self.is_posix:
             pass 
        else:
             obj_file = obj_file.with_suffix(".obj")

        # Check cache
        if cache and not cache.is_changed(source) and obj_file.exists():
            # Cache hit
            return obj_file

        from util.output import Printer
        Printer.action("COMPILE", f"{source.name} -> object")
        cmd = [compiler, "-c", str(source), "-o", str(obj_file)] + extra_cmd
        
        try:
            self.run_command(cmd, compiling=True)
            # We DONT add to output_files because:
            # 1. If cached, we want to persist them in cache
            # 2. If no-cache, we return the path and add to output_files in the caller
            if cache:
                cache.update_cache(source)
            return obj_file
        except Exception:
            return None

    def _handle_c_family_single_file(self, fp: Path):
        """
        Handle single C/C++ file compilation and execution.

        Args:
            fp (Path): Path to the C/C++ source file.
        """
        ext = fp.suffix.lower()
        lang = "c" if ext == ".c" else "cpp"
        default_compiler = "gcc" if lang == "c" else "g++"
        compiler = self.config.get_runner(lang, default_compiler)
        
        preset_flags = self.config.get_preset_flags(self.preset, lang)

        out_name = self.get_executable_path(fp)
        cmd = [compiler] + self.extra_flags + preset_flags + [str(fp), "-o", str(out_name)]
        
        self.run_command(cmd, compiling=True)
        self.output_files.append(out_name)
        self._execute_binary(out_name)

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
        from util.output import Printer
        Printer.info(f"Compiling {len(sources)} files using {max_workers} threads...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_source = {
                executor.submit(self._compile_object_file, compiler, src, base_cmd, self.cache): src 
                for src in sources
            }
            
            for future in concurrent.futures.as_completed(future_to_source):
                src = future_to_source[future]
                try:
                    obj_path = future.result()
                    if obj_path:
                        object_files.append(obj_path)
                        # If no cache, we need to ensure these object files are cleaned up
                        if self.cache is None:
                            self.output_files.append(obj_path)
                    else:
                        failed = True
                except Exception as e:
                    Printer.error(f"Failed to compile {src}: {e}")
                    failed = True

        if failed:
            from util.errors import ExecutionError
            raise ExecutionError("Build failed during compilation phase.")

        # Link
        out_name = self.get_executable_path(main_source)
        link_cmd = [compiler] + [str(o) for o in object_files] + ["-o", str(out_name)] + self.extra_flags + preset_flags
        
        if self.run_command(link_cmd, compiling=True):
            self.output_files.append(out_name)
            self._execute_binary(bin_path=out_name)
