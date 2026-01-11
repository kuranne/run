import requests
import os
import sys
import zipfile
import tempfile
import shutil
from pathlib import Path
from util.output import Printer, Colors

def update(repo: str, current_version: str):
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    
    try:
        Printer.action("CHECK", f"Checking for updates... (Current: {current_version})", Colors.CYAN)
        response = requests.get(api_url, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        latest_version = data['tag_name']
        
        if latest_version == current_version:
            Printer.action("UPDATE", "You are already on the latest version.")
            return

        Printer.warning(f"New version available: {latest_version}")
        
        # We prefer the zipball_url for full source code update
        download_url = data.get('zipball_url')
        
        if not download_url:
            # Fallback to assets if zipball_url is missing for some reason
            assets = data.get('assets', [])
            if not assets:
                Printer.error("No download assets found in the latest release.")
                return
            download_url = assets[0]['browser_download_url']

        Printer.action("DOWNLOAD", f"Downloading {latest_version}...", Colors.YELLOW)
        
        # Use a temporary directory for the zip file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_zip = Path(temp_dir) / "release.zip"
            
            # Download
            with requests.get(download_url, stream=True) as r:
                r.raise_for_status()
                with open(temp_zip, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            
            # Extract
            extract_path = Path(temp_dir) / "extracted"
            with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
                zip_ref.extractall(extract_path)
            
            # GitHub zips have a single root folder (e.g., repo-tag)
            extracted_items = list(extract_path.iterdir())
            if len(extracted_items) == 1 and extracted_items[0].is_dir():
                content_path = extracted_items[0]
            else:
                content_path = extract_path

            # Determine the installation directory
            # Path of this file: .../source/update.py, installation root is the parent of 'source'
            install_dir = Path(__file__).resolve().parent.parent
            
            Printer.action("INSTALL", f"Installing to {install_dir}...", Colors.CYAN)
            
            # Move files from content_path to install_dir
            for item in content_path.iterdir():
                dest = install_dir / item.name
                if item.is_dir():
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
                
        Printer.action("SUCCESS", f"Updated to {latest_version} successfully!", Colors.GREEN)
        
    except Exception as e:
        Printer.error(f"Failed to update: {e}")