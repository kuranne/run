from pathlib import Path
from util.errors import ConfigError
from shutil import which

class ScriptHandler:
    """
    Mixin class handling interpreted script languages.
    """
    
    def _get_interpreter_path(self, interpreter_names: list) -> str:
        """
        Find the first available interpreter from a list of options.

        Args:
            interpreter_names (list): List of interpreter names to check.

        Returns:
            str: Path to the first available interpreter.
            
        Raises:
            ConfigError: If no interpreter is found.
        """
        if hasattr(self, 'flags') and self.flags.get("sandbox"):
            return interpreter_names[0]
            
        for interpreter in interpreter_names:
            path = which(interpreter)
            if path:
                return path
        
        raise ConfigError(f"Interpreter not found. Tried: {', '.join(interpreter_names)}")

    def _detect_language_from_shebang(self, fp: Path) -> str:
        """
        Detect language from shebang line.

        Args:
            fp (Path): Path to the script file.

        Returns:
            str: Detected file extension or empty string if not detected.
        """
        try:
            with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                first_line = f.readline().strip()
                if first_line.startswith("#!"):
                    import re
                    if "python" in first_line:
                        return ".py"
                    elif "bash" in first_line or re.search(r'(?:/|\s)sh(?:\s|$)', first_line):
                        return ".sh"
                    elif "ruby" in first_line:
                        return ".rb"
                    elif "node" in first_line or "javascript" in first_line:
                        return ".js"
                    elif "perl" in first_line:
                        return ".pl"
                    elif "lua" in first_line:
                        return ".lua"
                    else:
                        from util.output import Printer
                        Printer.warning(f"Unknown shebang in {fp}: {first_line}")
                        return ""
        except Exception:
            pass
        return ""

    def _handle_bash_execution(self, fp: Path):
        """
        Handle Bash/Shell script execution.

        Args:
            fp (Path): Path to the shell script file.
        """
        try:
            prog = self._get_interpreter_path(["bash", "sh"])
        except ConfigError as e:
            from util.output import Printer
            Printer.error(str(e))
            return False
        
        return self.run_command([prog, str(fp)] + self.run_args)

    def _handle_ruby_execution(self, fp: Path):
        """
        Handle Ruby script execution.

        Args:
            fp (Path): Path to the Ruby source file.
        """
        try:
            prog = self._get_interpreter_path(["ruby"])
        except ConfigError as e:
            from util.output import Printer
            Printer.error(str(e))
            return False
        
        return self.run_command([prog, str(fp)] + self.run_args)

    def _handle_node_execution(self, fp: Path):
        """
        Handle Node.js/JavaScript script execution.

        Args:
            fp (Path): Path to the JavaScript source file.
        """
        try:
            prog = self._get_interpreter_path(["node", "nodejs"])
        except ConfigError as e:
            from util.output import Printer
            Printer.error(str(e))
            return False
        
        if self.flags.get("debug"):
            return self.run_command([prog, "--inspect-brk", str(fp)] + self.run_args)
        else:
            return self.run_command([prog, str(fp)] + self.run_args)

    def _handle_perl_execution(self, fp: Path):
        """
        Handle Perl script execution.

        Args:
            fp (Path): Path to the Perl source file.
        """
        try:
            prog = self._get_interpreter_path(["perl"])
        except ConfigError as e:
            from util.output import Printer
            Printer.error(str(e))
            return False
        
        return self.run_command([prog, str(fp)] + self.run_args)

    def _handle_lua_execution(self, fp: Path):
        """
        Handle Lua script execution.

        Args:
            fp (Path): Path to the Lua source file.
        """
        try:
            prog = self._get_interpreter_path(["lua", "luajit"])
        except ConfigError as e:
            from util.output import Printer
            Printer.error(str(e))
            return False
        
        return self.run_command([prog, str(fp)] + self.run_args)
