import hashlib
import json
import os
import sys
import re
from pathlib import Path
from typing import Dict, Any, Optional, List, Set

from util.output import Printer

class CacheManager:
    """
    Manages build caching using composite MD5 checksums.
    Stores cache data in ~/.cache/run_kuranne/<hash>/cache.json.
    """
    
    def __init__(self, project_root: Path = Path(".")):
        """Initialize CacheManager for the given project root."""
        project_root = project_root.absolute()
        project_hash = hashlib.md5(str(project_root).encode()).hexdigest()
        
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
        Uses MD5 of output path to ensure uniqueness.
        """
        path_hash = hashlib.md5(str(source_path.absolute()).encode()).hexdigest()
        
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
        """Calculate MD5 hash of a single file."""
        if not file_path.exists():
            return ""
        
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
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
            for match in include_pattern.finditer(content):
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
        Calculate composite MD5 hash of a file and its local header dependencies.
        """
        if not file_path.exists():
            return ""
        
        hash_md5 = hashlib.md5()
        self_hash = self.get_file_hash(file_path)
        hash_md5.update(self_hash.encode())

        c_exts = {'.c', '.cpp', '.cc'}
        if file_path.suffix.lower() in c_exts:
            includes = self._get_c_includes(file_path)
            for inc in sorted(includes, key=lambda p: str(p)):
                inc_hash = self.get_file_hash(inc)
                hash_md5.update(f"{inc.name}:{inc_hash}".encode())

        return hash_md5.hexdigest()

    def is_changed(self, file_path: Path) -> bool:
        """
        Check if a file or its dependencies have changed since last cache update.
        Returns True if changed or not in cache, False otherwise.
        """
        key = str(file_path.absolute())
        current_hash = self.get_composite_hash(file_path)
        
        if key not in self.cache_data:
            return True
        
        return self.cache_data[key] != current_hash

    def update_cache(self, file_path: Path):
        """Update the cache entry for a file."""
        key = str(file_path.absolute())
        self.cache_data[key] = self.get_composite_hash(file_path)
        self._save_cache()

    def clear(self):
        """Clear all cache."""
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
