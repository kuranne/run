import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomldecoder as tomllib  # Fallback if needed, though project requires 3.11+

from util.output import Printer

fp = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"

def version(file_path: Path = fp) -> str:
    """
    Read the version in pyproject.toml

    Args:
        file_path (Path): path to file    
    Returns:
        str: data in the file
    """
    try:        
        with open(file_path, "rb") as f:
            data = tomllib.load(f)
            __version__ = data.get("project", {}).get("version")
            return __version__

    except FileNotFoundError:
        Printer.warning(f"Not found {str(file_path)} in binary directory, please reinstall run")
        return None
    except Exception as e:
        Printer.error(f"Error reading version from {file_path}: {e}")
        return None

    