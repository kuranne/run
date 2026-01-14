from pathlib import Path
import subprocess as spc

class ScriptHandler:
    """
    Mixin class handling interpreted script languages.
    """
    def _detect_language_from_shebang(self, fp: Path) -> str:
        """
        Detect language from shebang line.

        Args:
            fp (Path): Path to the script file.

        Returns:
            str: Detected file extension or empty string if not detected.
        """
        try:
            with open(fp, 'r') as f:
                first_line = f.readline().strip()
                if first_line.startswith("#!"):
                    if "python" in first_line:
                        return ".py"
                    elif "bash" in first_line or "sh" in first_line:
                        # We don't have sh runner explicit, but...
                        # Maybe we can support shell scripts via generic runner?
                        # For now just detecting python is a good start.
                        pass
                    elif "ruby" in first_line:
                         # If we had ruby support...
                         pass
        except Exception:
            pass
        return ""

    def _handle_lua_execution(self, fp: Path):
        """
        Handle Lua script execution.

        Args:
            fp (Path): Path to the Lua source file.
        """
        check_cmd = "where" if not self.is_posix else "command -v"
        is_lua = spc.run(f"{check_cmd} lua", shell=True, capture_output=True).returncode == 0
        prog = "lua" if is_lua else "luajit"
        self.run_command([prog, str(fp)] + self.run_args)
