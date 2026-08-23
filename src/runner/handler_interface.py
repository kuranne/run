from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional, Any
from util.config import Config
from util.cache import CacheManager
from util.errors import ConfigError

@dataclass
class ExecutionContext:
    """
    Execution context providing runner state and helpers to handlers.
    """
    flags: Dict[str, Any]
    extra_flags: List[str]
    run_args: List[str]
    preset: Optional[str]
    config: Config
    cache: Optional[CacheManager]
    output_files: List[Path] = field(default_factory=list)
    is_posix: bool = True
    runner_ref: Any = None

    def run_command(self, cmd: List[str], use_shell: bool = False, compiling: bool = False) -> bool:
        """Execute a command via runner reference."""
        if self.runner_ref:
            return self.runner_ref.run_command(cmd, use_shell=use_shell, compiling=compiling)
        return True

    def get_executable_path(self, source_path: Path) -> Path:
        """Determine executable path for a given source."""
        if self.runner_ref:
            return self.runner_ref.get_executable_path(source_path)
        name = source_path.stem
        filename = f"{name}.exe" if not self.is_posix else f"{name}.out"
        return Path(f"./{filename}" if self.is_posix else filename)

    def execute_binary(self, bin_path: Path, args: Optional[List[str]] = None):
        """Execute a compiled binary."""
        if self.runner_ref:
            self.runner_ref._execute_binary(bin_path, args=args or [])

class LanguageHandler(ABC):
    """
    Abstract base class for all language handlers.
    """

    @abstractmethod
    def can_handle(self, path: Path, config: Config) -> bool:
        """
        Check if this handler can process the given file.

        Args:
            path (Path): Path to source file.
            config (Config): Project configuration.

        Returns:
            bool: True if handler supports this file.
        """
        pass

    @abstractmethod
    def run_single(self, fp: Path, ctx: ExecutionContext) -> None:
        """
        Execute or compile and run a single source file.

        Args:
            fp (Path): Path to source file.
            ctx (ExecutionContext): Current execution context.
        """
        pass

    def run_multi(self, paths: List[Path], ctx: ExecutionContext) -> None:
        """
        Execute or compile and run multiple source files.

        Args:
            paths (List[Path]): List of source files.
            ctx (ExecutionContext): Current execution context.
        """
        raise ConfigError(f"{self.__class__.__name__} does not support multi-file compilation.")
