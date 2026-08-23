import sys
from pathlib import Path
from typing import Optional
from importlib.metadata import version as pkg_version, PackageNotFoundError

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomldecoder as tomllib

from util.output import Printer

fp = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"

def version(file_path: Path = fp) -> Optional[str]:
    """
    Retrieve current package version from pyproject.toml or package metadata.

    Args:
        file_path (Path): Path to pyproject.toml fallback.

    Returns:
        Optional[str]: Version string if found, else None.
    """
    if file_path and file_path.exists():
        try:
            with open(file_path, "rb") as f:
                data = tomllib.load(f)
                v = data.get("project", {}).get("version")
                if v:
                    return str(v)
        except Exception as e:
            Printer.error(f"Error reading version from {file_path}: {e}")

    try:
        return pkg_version("run")
    except (PackageNotFoundError, Exception):
        return None

    