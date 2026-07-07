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
            # Fix read-only files before removing
            def unlink_readonly(func, path, excinfo):
                os.chmod(path, 0o777)
                func(path)
            shutil.rmtree(path, onerror=unlink_readonly)
        else:
            os.chmod(path, 0o777) if os.name != 'nt' else None
            os.remove(path)
    except Exception as e:
        log(f"Error removing {{path}}: {{e}}")
        time.sleep(1)

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
    zip_file_path = Path(r"{zip_file_path}")
    
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
                
        # Remove the downloaded zip file after successful update
        if zip_file_path.exists():
            try:
                os.remove(zip_file_path)
                log(f"Removed source zip file: {{zip_file_path}}")
            except Exception as e:
                log(f"Failed to remove zip file: {{e}}")
        
    except Exception as e:
        log(f"CRITICAL UPDATE FAILED: {{e}}")
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
    Update the runner by extracting a manually downloaded zip file in the root.
    """
    try:
        install_dir = Path(__file__).resolve().parent.parent.parent
        
        # Look for a .zip file in the install_dir
        zip_files = list(install_dir.glob("*.zip"))
        
        if not zip_files:
            Printer.error(f"No .zip file found in the project root ({install_dir}).")
            Printer.info("Please download the repository .zip file from GitHub and place it in the project root, then run update again.")
            return
            
        if len(zip_files) > 1:
            Printer.warning(f"Multiple .zip files found in {install_dir}.")
            Printer.info(f"Using the first one found: {zip_files[0].name}")
            
        zip_path = zip_files[0]
        
        Printer.action("UPDATE", f"Found zip file: {zip_path.name}", Colors.CYAN)
        if input("Extract and update? [y/N]: ") not in ("y", "Y"):
            return
            
        temp_dir = tempfile.mkdtemp(prefix="run_update_")
        temp_dir_path = Path(temp_dir)
        extract_path = temp_dir_path / "extracted"
        
        Printer.action("EXTRACT", f"Extracting {zip_path.name}...", Colors.YELLOW)
        content_path = _extract_zip(zip_path, extract_path)

        log_dir = Path(tempfile.gettempdir())
        log_file = log_dir / "run_update.log"

        script_content = UPDATE_SCRIPT_TEMPLATE.format(
            log_file=log_file.as_posix(),
            parent_pid=os.getpid(),
            src_dir=content_path.as_posix(),
            install_dir=install_dir.as_posix(),
            temp_root=temp_dir_path.as_posix(),
            zip_file_path=zip_path.as_posix()
        )
        
        script_path = temp_dir_path / "updater.py"
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_content)
            
        Printer.action("INSTALL", f"Starting background update process...", Colors.CYAN)
        Printer.info(f"The application will exit now. Check {log_file} for status.")
        
        python_exe = sys.executable
        
        if sys.platform == "win32":
             subprocess.Popen([python_exe, str(script_path)], creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
             subprocess.Popen([python_exe, str(script_path)], start_new_session=True)
             
        sys.exit(0)
        
    except Exception as e:
        Printer.error(f"Failed to update: {e}")