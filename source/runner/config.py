import sys
import shlex
from typing import List
from pathlib import Path
from runner.ui import Printer

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
        return self.data.get("runners", {}).get(lang, default)
    
    def get_preset_flags(self, preset_name: str, lang: str) -> List[str]:
        if not preset_name: return []
        flags = self.data.get("presets", {}).get(preset_name, {}).get(lang, "")
        return shlex.spilt(flags) if flags else []
        