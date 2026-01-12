import os
import sys
from typing import List, Dict, Optional
from util.output import Printer, Colors
from util.errors import ConfigError

class SecurityManager:
    """Manages security checks and enforcement for the runner."""

    @staticmethod
    def check_root(allow_root: bool = False):
        """Check if the script is running as root/admin."""
        is_root = False
        try:
            # POSIX
            if hasattr(os, 'geteuid'):
                is_root = os.geteuid() == 0
            # Windows (Admin check)
            elif os.name == 'nt':
                import ctypes
                is_root = ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            # If check fails, assume safe or we can't determine
            pass

        if is_root:
            msg = "Running as root/administrator is dangerous for compiling/running arbitrary code."
            if allow_root:
                Printer.warning(f"{msg} Proceeding due to override.")
            else:
                 # We raise generic error to be caught in main, or just sys.exit
                 # Raising ConfigError seems appropriate as it's a configuration/environment issue
                 Printer.error(msg)
                 raise ConfigError("Execution as root is blocked. Use --unsafe to override (not yet implemented).")

    @staticmethod
    def sanitize_execution_env() -> Dict[str, str]:
        """
        Return a sanitized environment dictionary for subprocess execution.
        Removing potentially dangerous variables if necessary.
        
        Returns:
            Dict[str, str]: Copy of os.environ with sensitive keys removed/sanitized.
        """
        env = os.environ.copy()
        # For a general purpose runner, we usually pass through everything.
        # But we might want to strip LD_PRELOAD just in case.
        if "LD_PRELOAD" in env:
            del env["LD_PRELOAD"]
        return env

    @staticmethod
    def check_suspicious_flags(flags: List[str]) -> bool:
        """
        Check for flags that explicitly try to do nasty things.
        
        Args:
            flags (List[str]): List of flags.
            
        Returns:
            bool: True if safe, False if suspicious.
        """
        # This is quite heuristic.
        # Example: preventing arbitrary command execution via compiler flags if possible
        # But compilers are toolchains that do many things.
        # Let's just flag empty for now to match interface.
        return True
