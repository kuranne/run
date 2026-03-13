import sys
import shlex
import os
from typing import List, Optional, Dict, Any
from pathlib import Path
from util.output import Printer

try:
    import tomllib
except ImportError:
    sys.exit("Error: Python 3.11+ required for tomllib")

class Config:
    """Configuration manager for the runner, handling TOML config loading and retrieval."""

    def _get_global_config_dir(self) -> Path:
        """
        Get global config directory for run configuration.
        Follows platform conventions using XDG on Linux/macOS and APPDATA on Windows.

        Returns:
            Path: Global configuration directory path.
        """
        if sys.platform == "win32":
            # Windows: %APPDATA%\run_kuranne
            appdata = os.getenv("APPDATA")
            if appdata:
                return Path(appdata) / "run_kuranne"
            else:
                return Path.home() / "AppData" / "Roaming" / "run_kuranne"
        else:
            # Linux/macOS: Follow XDG config home standard
            xdg_config_home = os.getenv("XDG_CONFIG_HOME")
            if xdg_config_home:
                return Path(xdg_config_home) / "run_kuranne"
            else:
                return Path.home() / ".config" / "run_kuranne"

    def __init__(self):
        """Initialize the Config manager, loading Run.toml from detected paths."""
        self.data: Dict[str, Any] = {}
        config_path = None
        
        # 1. Search in current workspace (up to 4 levels)
        current = Path.cwd()
        for i in range(4):  # 0=current, 1=parent, 2=grandparent, 3=great-grandparent
            target = current / "Run.toml"
            if target.exists():
                config_path = target
                break
            
            if current == current.parent:
                break
            current = current.parent

        # 2. If not found in workspace, check global config directory
        if not config_path:
            global_config_dir = self._get_global_config_dir()
            global_config_file = global_config_dir / "Run.toml"
            if global_config_file.exists():
                config_path = global_config_file
        
        if config_path:
            try:
                with open(config_path, "rb") as f:
                    self.data = tomllib.load(f)
                Printer.info(f"Loaded config: {config_path}")
            except Exception as e:
                Printer.error(f"Failed to parse {config_path}: {e}")
            
            # Validate after loading
            self.validate()

    def validate(self):
        """
        Validate the loaded configuration.
        
        Raises:
            ValueError: If configuration is invalid.
        """
        if not self.data:
            return

        # check 'runner'
        if "runner" in self.data:
             if not isinstance(self.data["runner"], dict):
                 raise ValueError("'runner' section must be a table (dict)")

        # check 'language'
        if "language" in self.data:
            if not isinstance(self.data["language"], dict):
                 raise ValueError("'language' section must be a table (dict)")
            
            for name, config in self.data["language"].items():
                if not isinstance(config, dict):
                    raise ValueError(f"Language '{name}' config must be a table")
                
                if "extensions" not in config:
                    raise ValueError(f"Language '{name}' missing required 'extensions' list")
                
                if not isinstance(config["extensions"], list):
                    raise ValueError(f"Language '{name}' 'extensions' must be a list")

                if "runner" not in config:
                     raise ValueError(f"Language '{name}' missing required 'runner' command")
    
    def get_runner(self, lang: str, default: str) -> str:
        """
        Get the runner command for a specific language.

        Args:
            lang (str): Language key (e.g., 'c', 'cpp', 'python').
            default (str): Default runner to use if not found.

        Returns:
            str: The runner command.
        """
        return self.data.get("runner", {}).get(lang, default)
    
    def get_preset_flags(self, preset_name: Optional[str], lang: str) -> List[str]:
        """
        Get compiler/interpreter flags for a specific preset and language.

        Args:
            preset_name (Optional[str]): Name of the preset (e.g., 'debug', 'release').
            lang (str): Language key.

        Returns:
            List[str]: List of flags.
        """
        if not preset_name: return []
        flags_data = self.data.get("preset", {}).get(preset_name, {}).get(lang, [])
        
        if isinstance(flags_data, list):
            return flags_data
        elif isinstance(flags_data, str):
            return shlex.split(flags_data)
        return []
    
    def get_custom_languages(self) -> Dict[str, Any]:
        """
        Returns all custom language configurations.

        Returns:
            Dict[str, Any]: Dictionary of language configurations.
        """
        return self.data.get("language", {})
    
    def get_language_by_extension(self, ext: str) -> Optional[Dict[str, Any]]:
        """
        Find language configuration by file extension.

        Args:
            ext (str): File extension (e.g., '.kt', '.zig').

        Returns:
            Optional[Dict[str, Any]]: Language configuration dictionary including name, or None if not found.
        """
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
        """
        Check if a file extension has a custom language configuration.

        Args:
            ext (str): File extension.

        Returns:
            bool: True if configured, False otherwise.
        """
        return self.get_language_by_extension(ext) is not None
        