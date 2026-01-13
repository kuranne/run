import requests
import os
import sys
import zipfile
import tempfile
import shutil
from pathlib import Path
from util.output import Printer, Colors

def _get_latest_version_from_raw(repo: str, branch: str = "workspace") -> str:
    """Fetch version string from a raw file in the repository."""
    raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/src/version.txt"
    
    response = requests.get(raw_url, timeout=5)
    response.raise_for_status()
    
    return response.text.strip()

def _download_file(url: str, dest_path: Path):
    """Download a file from a URL to a specified path."""
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

def _extract_zip(zip_path: Path, extract_to: Path) -> Path:
    """Extract a zip file and return the path to the content."""
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)
    
    extracted_items = list(extract_to.iterdir())
    if len(extracted_items) == 1 and extracted_items[0].is_dir():
        return extracted_items[0]
    return extract_to

def _install_files(src_dir: Path, dest_dir: Path):
    """Move files from source directory to destination directory."""
    for item in src_dir.iterdir():
        dest = dest_dir / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

def update(repo: str, current_version: str):
    """
    Update the runner by checking version.txt and downloading the source zip.
    """
    try:
        Printer.action("CHECK", f"Checking for updates... (Current: {current_version})", Colors.CYAN)
        
        # Get latest version from raw file
        latest_version = _get_latest_version_from_raw(repo, branch="main")
        
        if latest_version == current_version:
            Printer.action("UPDATE", "You are already on the latest version.")
            return

        Printer.warning(f"New version available: {latest_version}")
        
        # Create download URL (GitHub Source code zip pattern)
        download_url = f"https://github.com/{repo}/archive/refs/tags/{latest_version}.zip"

        Printer.action("DOWNLOAD", f"Downloading {latest_version}...", Colors.YELLOW)
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            temp_zip = temp_dir_path / "release.zip"
            extract_path = temp_dir_path / "extracted"
            
            _download_file(download_url, temp_zip)
            content_path = _extract_zip(temp_zip, extract_path)

            install_dir = Path(__file__).resolve().parent.parent
            
            Printer.action("INSTALL", f"Installing to {install_dir}...", Colors.CYAN)
            _install_files(content_path, install_dir)
                
        Printer.action("SUCCESS", f"Updated to {latest_version} successfully!", Colors.GREEN)
        
    except requests.RequestException as e:
        Printer.error(f"Network error (Check version.txt or Repo URL): {e}")
    except Exception as e:
        Printer.error(f"Failed to update: {e}")