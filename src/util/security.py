import os
import sys
from typing import List, Dict, Optional
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
        # Dynamic Linker / Loader (Linux / Unix)
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "LD_BIND_NOW",
        "LD_PROFILE",
        "GLIBC_TUNABLES",
        "GCONV_PATH",
        # macOS Dynamic Linker
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "DYLD_IMAGE_SUFFIX",
        "DYLD_PRINT_LIBRARIES",
        "DYLD_SHARED_CACHE_DIR",
        # Shell Startup / Injection
        "BASH_ENV",
        "ENV",
        "PROMPT_COMMAND",
        "IFS",
        "SHELLOPTS",
        # Python
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        # Node / JS
        "NODE_PATH",
        "NODE_OPTIONS",
        # Perl / Ruby / Lua
        "PERL5LIB",
        "PERL5OPT",
        "RUBYLIB",
        "RUBYOPT",
        "LUA_PATH",
        "LUA_CPATH",
        # Java
        "JAVA_TOOL_OPTIONS",
        "_JAVA_OPTIONS",
        "CLASSPATH",
        # Rust / Go
        "RUSTFLAGS",
        "RUSTC_WRAPPER",
        "GOFLAGS",
    )

    SAFE_ENV_WHITELIST = {
        "PATH", "HOME", "USER", "LOGNAME", "TERM", "LANG", "LC_ALL", "LC_CTYPE",
        "TMPDIR", "PWD", "TZ", "SHELL"
    }

    @staticmethod
    def is_dangerous_env_var(var: str) -> bool:
        """Check if an environment variable name is considered dangerous."""
        var_upper = var.upper()
        if var in SecurityManager.DANGEROUS_ENV_VARS or var_upper in SecurityManager.DANGEROUS_ENV_VARS:
            return True
        if var_upper.startswith("DYLD_") or var_upper.startswith("LD_") or var_upper.startswith("BASH_FUNC_"):
            return True
        return False

    @staticmethod
    def sanitize_execution_env(
        custom_env: Optional[Dict[str, str]] = None,
        strict_whitelist: bool = False
    ) -> Dict[str, str]:
        """
        Return a sanitized environment dictionary for subprocess execution.
        Removing potentially dangerous variables and merging custom variables safely.
        
        Args:
            custom_env (Optional[Dict[str, str]]): Custom environment variables to merge.
            strict_whitelist (bool): If True, only allow known safe environment variables.

        Returns:
            Dict[str, str]: Copy of os.environ or whitelist with sensitive keys removed.
        """
        if strict_whitelist:
            env = {
                k: v for k, v in os.environ.items()
                if k in SecurityManager.SAFE_ENV_WHITELIST and not SecurityManager.is_dangerous_env_var(k)
            }
        else:
            env = {
                k: v for k, v in os.environ.items()
                if not SecurityManager.is_dangerous_env_var(k)
            }

        if custom_env:
            for k, v in custom_env.items():
                if not SecurityManager.is_dangerous_env_var(k):
                    env[k] = str(v)
                else:
                    Printer.warning(f"Rejected dangerous custom environment variable: {k}")

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
