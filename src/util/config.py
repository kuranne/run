import sys
import shlex
from typing import List, Optional, Dict, Any
from pathlib import Path
from util.output import Printer

try:
    import tomllib
except ImportError:
    sys.exit("Error: Python 3.11+ required for tomllib")

class Config:
    """Configuration manager for the runner, handling TOML config loading and retrieval."""

    def __init__(self):
        """Initialize the Config manager, loading Run.toml from detected paths."""
        self.data: Dict[str, Any] = {}
        projects_directory = Path(__file__).resolve().parent.parent.parent
        
        # Check Current workspace instread, up to 3 levels (stop when found .git)
        current = Path.cwd()
        for i in range(4):  # 0=current, 1=p, 2=pp, 3=ppp
            target = current / "Run.toml"
            if target.exists():
                config_path = target
                break
            
            if (current / ".git").exists():
                break
                
            if current == current.parent:
                break
            current = current.parent

        # Search paths: 1. Current Dir, 2. Script Dir
        search_paths = [
            Path.cwd() / "Run.toml",
            current / "Run.toml"
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
        flags = self.data.get("preset", {}).get(preset_name, {}).get(lang, "")
        return shlex.split(flags) if flags else []
    
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
        