import sys
import shlex
from typing import List
from pathlib import Path
from util.output import Printer

try:
    import tomllib
except ImportError:
    sys.exit("Error: Python 3.11+ required for tomllib")

class Config:

    def __init__(self):
        self.data = {}
        projects_directory = Path(__file__).resolve().parent.parent.parent
        
        # Search paths: 1. Current Dir, 2. Script Dir
        search_paths = [
            Path.cwd() / "Run.toml",
            projects_directory / "Run.toml"
        ]
        
        config_path = None
        for p in search_paths:
            if p.exists():
                config_path = p
                break
        
        if config_path:
            try:
                with open(config_path, "rb") as f:
                    self.data = tomllib.load(f)
                Printer.info(f"Loaded config: {config_path}")
            except Exception as e:
                Printer.error(f"Failed to parse {config_path}: {e}")

    def get_runner(self, lang: str, default: str) -> str:
        return self.data.get("runner", {}).get(lang, default)
    
    def get_preset_flags(self, preset_name: str, lang: str) -> List[str]:
        if not preset_name: return []
        flags = self.data.get("preset", {}).get(preset_name, {}).get(lang, "")
        return shlex.split(flags) if flags else []
    
    def get_custom_languages(self) -> dict:
        """Returns all custom language configurations"""
        return self.data.get("language", {})
    
    def get_language_by_extension(self, ext: str) -> dict | None:
        """Find language configuration by file extension"""
        languages = self.get_custom_languages()
        for lang_name, lang_config in languages.items():
            extensions = lang_config.get("extensions", [])
            if ext in extensions:
                return {
                    "name": lang_name,
                    **lang_config
                }
        return None
    
    def is_custom_language_configured(self, ext: str) -> bool:
        """Check if a file extension has a custom language configuration"""
        return self.get_language_by_extension(ext) is not None
        