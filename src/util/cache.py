import hashlib
import json
import os
import sys
import re
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List, Set

from util.output import Printer

class CacheManager:
    """
    Manages build caching using composite SHA-256 checksums.
    Stores cache data in ~/.cache/run_kuranne/<hash>/cache.json.
    """
    
    def __init__(self, project_root: Path = Path(".")):
        """Initialize CacheManager for the given project root."""
        self._lock = threading.Lock()
        project_root = project_root.absolute()
        project_hash = hashlib.sha256(str(project_root).encode()).hexdigest()[:16]
        
        if sys.platform == "win32":
            base_cache = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        else:
            xdg_cache_home = os.getenv("XDG_CACHE_HOME")
            base_cache = Path(xdg_cache_home) if xdg_cache_home else Path.home() / ".cache"
            
        self.cache_dir = base_cache / "run_kuranne" / project_hash
        self.objs_dir = self.cache_dir / "objs"
        self.cache_file = self.cache_dir / "cache.json"
        self.cache_data: Dict[str, str] = {}
        self._load_cache()

    def get_object_path(self, source_path: Path) -> Path:
        """
        Get a unique path for the object file in the cache directory.
        Uses SHA-256 of output path to ensure uniqueness.
        """
        path_hash = hashlib.sha256(str(source_path.absolute()).encode()).hexdigest()[:16]
        
        if not self.objs_dir.exists():
            try:
                self.objs_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
                
        return self.objs_dir / f"{path_hash}_{source_path.name}.o"

    def _load_cache(self):
        """Load cache from disk."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r") as f:
                    self.cache_data = json.load(f)
            except (json.JSONDecodeError, IOError):
                Printer.warning("Failed to load cache, starting fresh.")
                self.cache_data = {}

    def _save_cache(self):
        """Save cache to disk."""
        with self._lock:
            if not self.cache_data:
                if self.cache_file.exists():
                    try:
                        self.cache_file.unlink()
                    except OSError:
                        pass
                
                if self.cache_dir.exists() and not any(self.cache_dir.iterdir()):
                    try:
                        self.cache_dir.rmdir()
                    except OSError:
                        pass
                return
                
            if not self.cache_dir.exists():
                try:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                except OSError:
                    return
            
            try:
                with open(self.cache_file, "w") as f:
                    json.dump(self.cache_data, f, indent=2)
            except IOError:
                pass

    def get_file_hash(self, file_path: Path) -> str:
        """Calculate SHA-256 hash of a single file."""
        if not file_path.exists():
            return ""
        
        hash_sha = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_sha.update(chunk)
            return hash_sha.hexdigest()
        except OSError:
            return ""

    def _get_c_includes(self, file_path: Path, visited: Optional[Set[Path]] = None) -> List[Path]:
        """
        Recursively extract local header dependencies (#include "...") for C/C++ files.
        """
        if visited is None:
            visited = set()
        
        includes = []
        if file_path in visited or not file_path.exists():
            return includes
        visited.add(file_path)

        c_exts = {'.c', '.cpp', '.cc', '.h', '.hpp', '.cxx', '.hxx'}
        if file_path.suffix.lower() not in c_exts:
            return includes

        include_pattern = re.compile(r'#\s*include\s*"([^"]+)"')
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            clean_content = re.sub(r'//.*?\n|/\*.*?\*/', '', content, flags=re.DOTALL)
            for match in include_pattern.finditer(clean_content):
                inc_rel = match.group(1)
                inc_path = (file_path.parent / inc_rel).resolve()
                if inc_path.exists() and inc_path not in visited:
                    includes.append(inc_path)
                    includes.extend(self._get_c_includes(inc_path, visited))
        except Exception:
            pass

        return includes

    def get_composite_hash(self, file_path: Path) -> str:
        """
        Calculate composite SHA-256 hash of a file and its local header dependencies.
        """
        if not file_path.exists():
            return ""
        
        hash_sha = hashlib.sha256()
        self_hash = self.get_file_hash(file_path)
        hash_sha.update(self_hash.encode())

        c_exts = {'.c', '.cpp', '.cc'}
        if file_path.suffix.lower() in c_exts:
            includes = self._get_c_includes(file_path)
            for inc in sorted(includes, key=lambda p: str(p)):
                inc_hash = self.get_file_hash(inc)
                hash_sha.update(f"{inc.name}:{inc_hash}".encode())

        return hash_sha.hexdigest()

    def is_changed(self, file_path: Path, output_path: Optional[Path] = None) -> bool:
        """
        Check if a file or its dependencies have changed since last cache update.
        If output_path is provided, also verifies that the output file exists and its hash matches.

        Args:
            file_path (Path): Source file path.
            output_path (Optional[Path]): Generated binary or object file path.

        Returns:
            bool: True if changed, binary missing/mismatched, or not in cache; False otherwise.
        """
        key = str(file_path.absolute())
        current_source_hash = self.get_composite_hash(file_path)
        
        with self._lock:
            if key not in self.cache_data:
                return True
            
            entry = self.cache_data[key]
            # Handle legacy string format or dict format
            if isinstance(entry, dict):
                if entry.get("source_hash") != current_source_hash:
                    return True
                
                if output_path is not None:
                    if not output_path.exists():
                        return True
                    current_bin_hash = self.get_file_hash(output_path)
                    if entry.get("binary_hash") != current_bin_hash:
                        return True
                return False
            else:
                # Legacy format: entry is string hash
                if entry != current_source_hash:
                    return True
                if output_path is not None and not output_path.exists():
                    return True
                return False

    def update_cache(self, file_path: Path, output_path: Optional[Path] = None):
        """
        Update the cache entry for a file and optionally record output binary hash.

        Args:
            file_path (Path): Source file path.
            output_path (Optional[Path]): Generated binary or object file path.
        """
        key = str(file_path.absolute())
        comp_hash = self.get_composite_hash(file_path)
        
        bin_hash = self.get_file_hash(output_path) if output_path and output_path.exists() else None
        
        entry = {
            "source_hash": comp_hash,
            "binary_hash": bin_hash,
            "binary_path": str(output_path.absolute()) if output_path else None
        }
        
        with self._lock:
            self.cache_data[key] = entry
        self._save_cache()

    def clear(self):
        """Clear all cache."""
        with self._lock:
            self.cache_data = {}
            if self.cache_file.exists():
                try:
                    self.cache_file.unlink()
                except OSError:
                    pass
            
            if self.cache_dir.exists() and not any(self.cache_dir.iterdir()):
                try:
                    self.cache_dir.rmdir()
                except OSError:
                    pass
