from pathlib import Path
from typing import Optional
from util.output import Printer

class RustHandler:
    """
    Mixin class handling Cargo/Rust specific operations.
    """
    def _find_cargo_toml(self, start_path: Path) -> Optional[Path]:
        """
        Walk up from start_path to find Cargo.toml.
        
        Args:
            start_path (Path): Path to start searching from.

        Returns:
            Optional[Path]: Path to Cargo.toml if found, else None.
        """
        current = start_path.absolute()
        if current.is_file():
            current = current.parent
            
        for _ in range(3): # Check up to 3 levels up
            toml = current / "Cargo.toml"
            if toml.exists():
                return toml
            current = current.parent
        return None

    def _get_cargo_package_name(self, toml_path: Path) -> Optional[str]:
        """
        Simple parsing to get package name from Cargo.toml.

        Args:
            toml_path (Path): Path to Cargo.toml.

        Returns:
            Optional[str]: Package name if found, else None.
        """
        try:
            with open(toml_path, 'r', encoding='utf-8') as f:
                in_package = False
                for line in f:
                    line = line.strip()
                    if line == "[package]":
                        in_package = True
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        in_package = False
                    
                    if in_package and line.startswith("name"):
                        # name = "project_name"
                        parts = line.split('=')
                        if len(parts) >= 2:
                            return parts[1].strip().strip('"').strip("'")
        except Exception:
            pass
        return None

    def run_cargo_mode(self, toml_path: Optional[Path] = None):
        """
        Handle cargo execution logic: run -q OR build && run.

        Args:
            toml_path (Optional[Path]): Path to Cargo.toml. Defaults to "Cargo.toml" in cwd.
        """
        # If path not provided, assume current dir
        if not toml_path:
            toml_path = Path("Cargo.toml")

        is_release = "--release" in self.extra_flags
        
        if is_release:
            # Case: Build Release -> Run Binary
            Printer.info("Building release...")
            build_cmd = ["cargo", "build"] + self.extra_flags
            if not self.run_command(build_cmd, compiling=True):
                return
            
            pkg_name = self._get_cargo_package_name(toml_path)
            if not pkg_name:
                Printer.error("Could not parse package name from Cargo.toml")
                return

            bin_name = f"{pkg_name}.exe" if not self.is_posix else pkg_name
            target_bin = toml_path.parent / "target" / "release" / bin_name
            
            if target_bin.exists():
                self._execute_binary(target_bin)
            else:
                Printer.error(f"Binary not found at {target_bin}")
        else:
            # Case: Default Run Quiet
            # Note: -q comes before --flags to ensure cargo itself is quiet
            cmd = ["cargo", "run", "-q"] + self.extra_flags
            self.run_command(cmd)