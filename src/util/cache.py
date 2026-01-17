import hashlib
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional

from util.output import Printer, Colors
from util.errors import RunError

class CacheManager:
    """
    Manages build caching using MD5 checksums.
    Stores cache data in .run_cache/cache.json relative to the project root or current dir.
    """
    
    def __init__(self, project_root: Path = Path(".")):
        self.cache_dir = project_root / ".run_cache"
        self.objs_dir = self.cache_dir / "objs"
        self.cache_file = self.cache_dir / "cache.json"
        self.cache_data: Dict[str, str] = {}
        self._load_cache()
        
        # Ensure objects dir exists
        # if not self.objs_dir.exists():
        #     try:
        #         self.objs_dir.mkdir(parents=True, exist_ok=True)
        #     except OSError:
        #         pass

    def get_object_path(self, source_path: Path) -> Path:
        """
        Get a unique path for the object file in the cache directory.
        Uses MD5 of output path to ensure uniqueness.
        """
        # We use hash of absolute path to create a unique filename
        # e.g. source.c -> objs/hash_source.o
        path_hash = hashlib.md5(str(source_path.absolute()).encode()).hexdigest()
        
        # Ensure objects dir exists lazily
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
            # If cache is empty, try to remove cache file and directory
            if self.cache_file.exists():
                try:
                    self.cache_file.unlink()
                except OSError:
                    pass
            
            # If directory is empty, remove it
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
                return # Cannot create cache dir, ignore
        
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self.cache_data, f, indent=2)
        except IOError:
            pass # Failed to save, ignore

    def get_file_hash(self, file_path: Path) -> str:
        """Calculate MD5 hash of a file."""
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

    def is_changed(self, file_path: Path) -> bool:
        """
        Check if a file has changed since last cache update.
        Returns True if changed or not in cache, False otherwise.
        """
        key = str(file_path.absolute())
        current_hash = self.get_file_hash(file_path)
        
        if key not in self.cache_data:
            return True
        
        return self.cache_data[key] != current_hash

    def update_cache(self, file_path: Path):
        """Update the cache entry for a file."""
        key = str(file_path.absolute())
        self.cache_data[key] = self.get_file_hash(file_path)
        self._save_cache()

    def clear(self):
        """Clear all cache."""
        self.cache_data = {}
        if self.cache_file.exists():
            try:
                self.cache_file.unlink()
            except OSError:
                pass
        
        # If directory is empty, remove it
        if self.cache_dir.exists() and not any(self.cache_dir.iterdir()):
            try:
                self.cache_dir.rmdir()
            except OSError:
                pass
