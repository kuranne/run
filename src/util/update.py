import requests
import os
import sys
import zipfile
import tempfile
import shutil
import subprocess
import time
from pathlib import Path
from util.output import Printer, Colors

UPDATE_SCRIPT_TEMPLATE = """
import os
import sys
import shutil
import time
import subprocess
from pathlib import Path

def log(msg):
    with open("{log_file}", "a") as f:
        f.write(msg + "\\n")

def main():
    log("Starting update process...")
    
    # Wait for parent process to exit
    pid = {parent_pid}
    log(f"Waiting for parent process {{pid}} to exit...")
    try:
        while True:
            try:
                os.kill(pid, 0)
                time.sleep(0.5)
            except OSError:
                break
    except Exception as e:
        log(f"Error waiting for process: {{e}}")
    
    log("Parent process exited. Starting update...")
    
    src_dir = Path("{src_dir}")
    install_dir = Path("{install_dir}")
    
    try:
        # Copy files
        log(f"Copying files from {{src_dir}} to {{install_dir}}")
        for item in src_dir.iterdir():
            dest = install_dir / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        
        log("Files copied successfully.")
        
        # Run setup
        setup_script = install_dir / ("setup.ps1" if os.name == "nt" else "setup.sh")
        if setup_script.exists():
            log(f"Running setup script: {{setup_script}}")
            # Make sure it's executable
            if os.name != "nt":
                os.chmod(setup_script, 0o755)
                cmd = ["/bin/bash", str(setup_script)]
            else:
                cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(setup_script)]
                
            subprocess.run(cmd, check=True, cwd=install_dir)
            log("Setup completed successfully.")
        else:
            log("Setup script not found, skipping.")
            
    except Exception as e:
        log(f"Update failed: {{e}}")
        sys.exit(1)
    finally:
        # Cleanup temp dir
        try:
            shutil.rmtree(src_dir.parent.parent) # Clean up the temp dir created by mkdtemp
            log("Cleaned up temporary files.")
        except Exception as e:
            log(f"Failed to cleanup: {{e}}")

if __name__ == "__main__":
    main()
"""

def _get_latest_version_from_raw(repo: str, branch: str = "main") -> str:
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

def update(repo: str, current_version: str):
    """
    Update the runner by checking version.txt and downloading the source zip.
    """
    try:
        Printer.action("CHECK", f"Checking for updates... (Current: {current_version})", Colors.CYAN)
        
        # Get latest version from raw file
        latest_version = _get_latest_version_from_raw(repo=repo)
        
        if latest_version == current_version:
            Printer.action("UPDATE", "You are already on the latest version.")
            return

        Printer.warning(f"New version available: {latest_version}")
        
        # Create download URL (GitHub Source code zip pattern)
        download_url = f"https://github.com/{repo}/archive/refs/tags/{latest_version}.zip"

        Printer.action("DOWNLOAD", f"Downloading {latest_version}...", Colors.YELLOW)
        
        # Create a persistent temp dir that the external script can access
        temp_dir = tempfile.mkdtemp(prefix="run_update_")
        temp_dir_path = Path(temp_dir)
        temp_zip = temp_dir_path / "release.zip"
        extract_path = temp_dir_path / "extracted"
        
        _download_file(download_url, temp_zip)
        content_path = _extract_zip(temp_zip, extract_path)

        install_dir = Path(__file__).resolve().parent.parent.parent
        
        # Determine log file location based on OS
        log_dir = Path(tempfile.gettempdir())
        log_file = log_dir / "run_update.log"

        # Create the external update script
        script_content = UPDATE_SCRIPT_TEMPLATE.format(
            log_file=log_file.as_posix(),
            parent_pid=os.getpid(),
            src_dir=content_path.as_posix(),
            install_dir=install_dir.as_posix()
        )
        
        script_path = temp_dir_path / "updater.py"
        with open(script_path, "w") as f:
            f.write(script_content)
            
        Printer.action("INSTALL", f"Starting background update process...", Colors.CYAN)
        Printer.info(f"The application will exit now. Check /tmp/run_update.log for status.")
        
        # Spawn the external process
        if sys.platform == "win32":
             subprocess.Popen(["python", str(script_path)], creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
             subprocess.Popen(["python3", str(script_path)], start_new_session=True)
             
        # Exit immediately
        sys.exit(0)
        
    except requests.RequestException as e:
        Printer.error(f"Network error (Check version.txt or Repo URL): {e}")
    except Exception as e:
        Printer.error(f"Failed to update: {e}")