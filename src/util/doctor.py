import shutil
import subprocess
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from util.output import Printer, Colors

class Doctor:
    """System toolchain and environment diagnostics scanner."""

    TOOLCHAIN_GROUPS: Dict[str, List[Tuple[str, str]]] = {
        "C / C++": [
            ("gcc", "--version"),
            ("g++", "--version"),
            ("clang", "--version"),
            ("clang++", "--version"),
            ("make", "--version"),
            ("cmake", "--version"),
            ("ninja", "--version"),
        ],
        "Rust": [
            ("rustc", "--version"),
            ("cargo", "--version"),
        ],
        "Python": [
            ("python3", "--version"),
            ("pip", "--version"),
        ],
        "Java": [
            ("javac", "-version"),
            ("java", "-version"),
        ],
        "JavaScript / TypeScript": [
            ("node", "--version"),
            ("bun", "--version"),
            ("deno", "--version"),
            ("npm", "--version"),
            ("ts-node", "--version"),
        ],
        "Go": [
            ("go", "version"),
        ],
        "Zig": [
            ("zig", "version"),
        ],
    }

    @classmethod
    def check_binary(cls, binary: str, version_arg: str = "--version") -> Optional[str]:
        """
        Check if a binary is installed and retrieve its version.

        Args:
            binary (str): Binary executable name.
            version_arg (str): Argument to query version.

        Returns:
            Optional[str]: First line of version string if found, otherwise None.
        """
        path = shutil.which(binary)
        if not path:
            return None

        try:
            res = subprocess.run(
                [binary, version_arg],
                capture_output=True,
                text=True,
                timeout=2.0
            )
            raw = (res.stdout or res.stderr or "").strip()
            if raw:
                first_line = raw.splitlines()[0].strip()
                # Shorten very long version outputs
                return first_line if len(first_line) < 60 else first_line[:57] + "..."
            return path
        except Exception:
            return path

    @classmethod
    def diagnose(cls) -> int:
        """
        Run diagnostics across all registered toolchains and print results.

        Returns:
            int: 0 on completion.
        """
        print(f"\n{Colors.BOLD}{Colors.CYAN}=== System Toolchain Diagnostics ==={Colors.RESET}\n")
        
        found_total = 0
        checked_total = 0

        for group_name, tools in cls.TOOLCHAIN_GROUPS.items():
            print(f"{Colors.BOLD}{group_name}:{Colors.RESET}")
            for binary, varg in tools:
                checked_total += 1
                version_info = cls.check_binary(binary, varg)
                if version_info:
                    found_total += 1
                    print(f"  {Colors.GREEN}[ OK ]{Colors.RESET} {binary:<12} : {version_info}")
                else:
                    print(f"  {Colors.GRAY}[ -- ]{Colors.RESET} {binary:<12} : Not installed / not in PATH")
            print()

        # Check Python venv status
        venv_active = os.getenv("VIRTUAL_ENV") or Path(".venv").exists()
        venv_str = "Active / Detected" if venv_active else "Not detected"
        print(f"{Colors.BOLD}Environment:{Colors.RESET}")
        print(f"  {Colors.GREEN if venv_active else Colors.GRAY}[ {'OK' if venv_active else '--'} ]{Colors.RESET} {'Python venv':<12} : {venv_str}")
        print(f"\n{Colors.CYAN}Detected {found_total}/{checked_total} development tools.{Colors.RESET}\n")
        return 0
