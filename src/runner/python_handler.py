from pathlib import Path
from util.errors import RunError

class PythonHandler:
    """
    Mixin class handling Python-specific operations.
    """

    def _get_python_executable(self) -> str:
        """
        Check for .venv or .env and return python path, else system default.

        Returns:
            str: Path to the python executable or command name.
        """
        potential_venvs = [".venv", ".env"]
        # Check in current working directory
        if not self.flags.get("sandbox"):
            for venv in potential_venvs:
                venv_path = Path(venv)
                if venv_path.is_dir():
                    if self.is_posix:
                        py_path = venv_path / "bin" / "python"
                    else:
                        py_path = venv_path / "Scripts" / "python.exe"
                    
                    if py_path.exists():
                        from util.output import Printer
                        Printer.info(f"Using venv: {venv}")
                        return str(py_path)

        if self.flags.get("sandbox"):
            return "python3"

        from shutil import which
        possible_exec = ["python", "python3"]
        for exe in possible_exec:
            py_path = which(exe)
            if py_path:
                return py_path
        else:
            raise RunError("Not found python runtime path.")

    def _handle_python_execution(self, fp: Path):
        """
        Handle Python script execution.

        Args:
            fp (Path): Path to the Python source file.
        """
        try:
            prog = self._get_python_executable()
        except RunError as e:
            from util.output import Printer
            Printer.error(str(e))
            return

        if self.flags.get("debug"):
            return self.run_command([prog, "-m", "pdb", str(fp)] + self.run_args)
        else:
            return self.run_command([prog, str(fp)] + self.run_args)
