from pathlib import Path
from util.output import Printer

fp = Path(__file__).resolve().parent.parent / "version.txt"

def version(file_path: Path = fp) -> str:
    """
    Read the version in src/version.txt

    Args:
        file_path (Path): path to file    
    Returns:
        str: data in the file
    """
    try:        
        with open(file_path, "r") as f:
            __version__ = "".join(f.read().split())
            return __version__

    except FileNotFoundError:
        Printer.warning(f"Not found {str(file_path)} in binary directory, please reinstall run")
        return None
    