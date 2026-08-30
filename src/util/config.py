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
    _logged_config_paths = set()

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
                if "--_complete" not in sys.argv and "--completion" not in sys.argv:
                    if config_path not in Config._logged_config_paths:
                        Printer.info(f"Loaded config: {config_path}")
                        Config._logged_config_paths.add(config_path)
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

        if "runners" in self.data and not isinstance(self.data["runners"], dict):
            raise ValueError("'runners' section must be a table (dict)")

        if "presets" in self.data and not isinstance(self.data["presets"], dict):
            raise ValueError("'presets' section must be a table (dict)")
            
        if "core" in self.data and not isinstance(self.data["core"], dict):
            raise ValueError("'core' section must be a table (dict)")

        if "tasks" in self.data and not isinstance(self.data["tasks"], dict):
            raise ValueError("'tasks' section must be a table (dict)")

        if "projects" in self.data and not isinstance(self.data["projects"], dict):
            raise ValueError("'projects' section must be a table (dict)")

        if "sandbox" in self.data:
            if not isinstance(self.data["sandbox"], dict):
                raise ValueError("'sandbox' section must be a table (dict)")
            sandbox_cfg = self.data["sandbox"]
            if "image" in sandbox_cfg:
                img = sandbox_cfg["image"]
                if not isinstance(img, str) or not img.strip():
                    raise ValueError("sandbox 'image' must be a non-empty string")
                if img.strip().startswith("-") or " " in img.strip():
                    raise ValueError(f"Invalid sandbox image name '{img}'")
            if "dockerfile" in sandbox_cfg:
                df = sandbox_cfg["dockerfile"]
                if not isinstance(df, str) or not df.strip():
                    raise ValueError("sandbox 'dockerfile' must be a non-empty string")
                df_path = Path(df)
                if not df_path.exists() and not (Path.cwd() / df_path).exists():
                    raise ValueError(f"Configured sandbox Dockerfile '{df}' not found")
            if "compose" in sandbox_cfg:
                compose = sandbox_cfg["compose"]
                if not isinstance(compose, str) or not compose.strip():
                    raise ValueError("sandbox 'compose' must be a non-empty string")
                comp_path = Path(compose)
                if not comp_path.exists() and not (Path.cwd() / comp_path).exists():
                    raise ValueError(f"Configured sandbox compose file '{compose}' not found")
            if "compose_service" in sandbox_cfg:
                svc = sandbox_cfg["compose_service"]
                if not isinstance(svc, str) or not svc.strip():
                    raise ValueError("sandbox 'compose_service' must be a non-empty string")
            if "sandbox_net" in sandbox_cfg and not isinstance(sandbox_cfg["sandbox_net"], bool):
                raise ValueError("sandbox 'sandbox_net' must be a boolean")
            if "restrict" in sandbox_cfg and not isinstance(sandbox_cfg["restrict"], bool):
                raise ValueError("sandbox 'restrict' must be a boolean")

        if "languages" in self.data:
            if not isinstance(self.data["languages"], list):
                raise ValueError("'languages' section must be an array of tables ([[languages]])")
            
            for config in self.data["languages"]:
                if not isinstance(config, dict):
                    raise ValueError("Language config must be a table")
                
                if "name" not in config:
                    raise ValueError("Language config missing required 'name' field")
                    
                name = config.get("name")
                
                if "extensions" not in config:
                    raise ValueError(f"Language '{name}' missing required 'extensions' list")
                
                if not isinstance(config["extensions"], list):
                    raise ValueError(f"Language '{name}' 'extensions' must be a list")

                if "runner" not in config:
                     raise ValueError(f"Language '{name}' missing required 'runner' command")
    
    def get_runner(self, lang: str, default: str) -> str:
        """
        Get the runner command for a specific language.
        """
        # Fallback to old "runner" table if someone still uses it temporarily
        runners = self.data.get("runners", self.data.get("runner", {}))
        return runners.get(lang, default)
    
    def get_tasks(self) -> Dict[str, Any]:
        """
        Get custom tasks from configuration.
        """
        tasks = self.data.get("tasks", {})
        return tasks if isinstance(tasks, dict) else {}

    def get_sandbox_config(self) -> Dict[str, Any]:
        """
        Get global sandbox configuration.
        """
        sandbox = self.data.get("sandbox", {})
        return sandbox if isinstance(sandbox, dict) else {}

    def get_projects(self) -> Dict[str, Dict[str, Any]]:
        """
        Get project manifest detectors, with user overrides/custom definitions taking priority.
        """
        merged = {}
        user_projects = self.data.get("projects", {})
        if isinstance(user_projects, dict):
            for name, cfg in user_projects.items():
                if isinstance(cfg, dict):
                    merged[name] = cfg

        default_projects = {
            "cargo": {
                "file": "Cargo.toml",
                "command": "cargo run -q"
            },
            "go": {
                "file": "go.mod",
                "command": "go run ."
            },
            "zig": {
                "file": "build.zig",
                "command": "zig build run"
            },
            "cmake": {
                "file": "CMakeLists.txt",
                "build": "cmake -B build && cmake --build build",
                "run": "./build/app"
            },
            "make": {
                "file": "Makefile",
                "command": "make"
            }
        }
        for name, cfg in default_projects.items():
            if name not in merged:
                merged[name] = cfg

        return merged

    def get_preset_flags(self, preset_name: Optional[str], lang: str) -> List[str]:
        """
        Get compiler/interpreter flags for a specific preset and language.
        """
        if not preset_name: return []
        # Support both old 'preset' and new 'presets'
        presets = self.data.get("presets", self.data.get("preset", {}))
        flags_data = presets.get(preset_name, {}).get(lang, [])
        
        if isinstance(flags_data, list):
            return flags_data
        elif isinstance(flags_data, str):
            return shlex.split(flags_data)
        return []
    
    def get_custom_languages(self) -> Dict[str, Any]:
        """
        Returns all custom language configurations.
        """
        langs = {}
        # Support old format mapping for backwards compatibility if needed, but prioritize new format
        if "language" in self.data and isinstance(self.data["language"], dict):
            for name, config in self.data["language"].items():
                langs[name] = {"name": name, **config}
                
        # New format [[languages]]
        if "languages" in self.data and isinstance(self.data["languages"], list):
            for config in self.data["languages"]:
                langs[config["name"]] = config
                
        return langs
    
    def get_language_by_extension(self, ext: str) -> Optional[Dict[str, Any]]:
        """
        Find language configuration by file extension.
        """
        languages = self.get_custom_languages()
        for lang_name, lang_config in languages.items():
            extensions = lang_config.get("extensions", [])
            if ext in extensions:
                return lang_config
        return None
    
    def is_custom_language_configured(self, ext: str) -> bool:
        """
        Check if a file extension has a custom language configuration.
        """
        return self.get_language_by_extension(ext) is not None
    
    def get_exclude(self) -> Optional[Dict[str, Any]]:
        """
        Get exclude extensions and files.
        """
        core = self.data.get("core", {})
        old_exclude = self.data.get("exclude", {})
        
        return {
            "files": core.get("exclude_files", old_exclude.get("files", [])),
            "extensions": core.get("exclude_extensions", old_exclude.get("extensions", []))
        }