import os
import sys
from typing import List, Dict
from util.output import Printer
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
                Printer.error(msg)
                raise ConfigError("Execution as root is blocked. Use --unsafe to override.")

    DANGEROUS_ENV_VARS = (
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "NODE_PATH",
        "NODE_OPTIONS",
        "PERL5LIB",
        "RUBYLIB",
    )

    @staticmethod
    def sanitize_execution_env() -> Dict[str, str]:
        """
        Return a sanitized environment dictionary for subprocess execution.
        Removing potentially dangerous variables if necessary.
        
        Returns:
            Dict[str, str]: Copy of os.environ with sensitive keys removed/sanitized.
        """
        env = os.environ.copy()
        for var in SecurityManager.DANGEROUS_ENV_VARS:
            env.pop(var, None)
        return env

    @staticmethod
    def check_suspicious_flags(flags: List[str]) -> bool:
        """
        Check for flags that explicitly try to do arbitrary code execution or plugin loading.
        
        Args:
            flags (List[str]): List of flags.
            
        Returns:
            bool: True if safe, False if suspicious.
        """
        dangerous_patterns = [
            "-Wl,-rpath",
            "-Wl,--wrap",
            "-fplugin=",
            "-x assembler",
        ]
        for flag in flags:
            for pattern in dangerous_patterns:
                if pattern in flag:
                    Printer.warning(f"Suspicious flag detected: {flag}")
                    return False
        return True
