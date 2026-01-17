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
    try:
        with open(r"{log_file}", "a", encoding="utf-8") as f:
            f.write(str(msg) + "\\n")
    except:
        pass

def force_remove(path):
    if not path.exists(): return
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            os.remove(path)
    except Exception as e:
        log(f"Error removing {{path}}: {{e}}")
        # On Windows, sometimes file is locked briefly, retry once
        time.sleep(0.5)
        try:
            if path.is_dir(): shutil.rmtree(path)
            else: os.remove(path)
        except:
            pass

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
    
    # Give it an extra second to release file locks (important on Windows)
    time.sleep(1)
    
    src_dir = Path(r"{src_dir}")
    install_dir = Path(r"{install_dir}")
    temp_root = Path(r"{temp_root}")
    
    try:
        log(f"Copying files from {{src_dir}} to {{install_dir}}")
        
        # Copy logic: Iterate source and overwrite destination
        for item in src_dir.iterdir():
            dest = install_dir / item.name
            
            if dest.exists():
                force_remove(dest)
                
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        
        log("Files copied successfully.")
        
        # Run setup
        setup_script = install_dir / ("setup.ps1" if os.name == "nt" else "setup.sh")
        if setup_script.exists():
            log(f"Running setup script: {{setup_script}}")
            
            if os.name != "nt":
                os.chmod(setup_script, 0o755)
                cmd = ["/bin/bash", str(setup_script)]
            else:
                cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(setup_script)]
            
            # Run setup but don't fail the whole update if setup script has minor errors
            try:    
                subprocess.run(cmd, check=True, cwd=install_dir)
                log("Setup completed successfully.")
            except subprocess.CalledProcessError as e:
                log(f"Setup script returned error: {{e}} (Update files likely preserved)")
        
    except Exception as e:
        log(f"CRITICAL UPDATE FAILED: {{e}}")
        # Optional: Try to rollback here if you had a backup
        sys.exit(1)
    finally:
        # Cleanup temp dir explicitly
        try:
            log(f"Cleaning up temp: {{temp_root}}")
            shutil.rmtree(temp_root, ignore_errors=True)
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
        
        latest_version = _get_latest_version_from_raw(repo=repo)
        
        if latest_version == current_version:
            Printer.action("UPDATE", "You are already on the latest version.")
            return

        Printer.warning(f"New version available: {latest_version}")
        if input("Update?[y/N]: ") not in ("y", "Y"):
            return

        # Handle tags with or without 'v' prefix if needed
        # GitHub tags might be "v1.0.0" but version.txt is "1.0.0"
        tag_name = latest_version
        if not tag_name.startswith("v") and "." in tag_name: 
            # Check logic here depends on your repo naming convention
            # For now, trust the version string or prepend 'v' if your tags use it
            pass 

        download_url = f"https://github.com/{repo}/archive/refs/tags/{tag_name}.zip"

        Printer.action("DOWNLOAD", f"Downloading {latest_version}...", Colors.YELLOW)
        
        temp_dir = tempfile.mkdtemp(prefix="run_update_")
        temp_dir_path = Path(temp_dir)
        temp_zip = temp_dir_path / "release.zip"
        extract_path = temp_dir_path / "extracted"
        
        _download_file(download_url, temp_zip)
        content_path = _extract_zip(temp_zip, extract_path)

        install_dir = Path(__file__).resolve().parent.parent.parent
        
        log_dir = Path(tempfile.gettempdir())
        log_file = log_dir / "run_update.log"

        # Insert r (raw string) at the front of path in template -> Prevent escape characters
        # Then send temp_dir_path from parent to cleanup
        script_content = UPDATE_SCRIPT_TEMPLATE.format(
            log_file=log_file.as_posix(),
            parent_pid=os.getpid(),
            src_dir=content_path.as_posix(),
            install_dir=install_dir.as_posix(),
            temp_root=temp_dir_path.as_posix() 
        )
        
        script_path = temp_dir_path / "updater.py"
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_content)
            
        Printer.action("INSTALL", f"Starting background update process...", Colors.CYAN)
        Printer.info(f"The application will exit now. Check {log_file} for status.")
        
        # sys.executable ensure that same python
        python_exe = sys.executable
        
        if sys.platform == "win32":
             # Use CREATE_NEW_CONSOLE to detach effectively
             subprocess.Popen([python_exe, str(script_path)], creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
             # Use start_new_session to detach
             subprocess.Popen([python_exe, str(script_path)], start_new_session=True)
             
        sys.exit(0)
        
    except requests.RequestException as e:
        Printer.error(f"Network error: {e}")
    except Exception as e:
        Printer.error(f"Failed to update: {e}")