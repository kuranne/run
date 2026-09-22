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

    @staticmethod
    def is_run_shebang(fp: Path) -> bool:
        """
        Check if a file begins with a shebang invoking 'run'.
        e.g., #!/usr/bin/env run, #!/usr/bin/run, #!/bin/run, #!/usr/local/bin/run
        """
        try:
            if fp.is_file():
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    first_line = f.readline().strip()
                    if first_line.startswith("#!"):
                        import re
                        return bool(re.search(r'(?:/|\s)run(?:\s|$)', first_line))
        except Exception:
            pass
        return False

    @staticmethod
    def extract_inline_directives(fp: Path) -> list:
        """
        Scan the top comments of a source file for inline compiler/runner directives.
        Supports:
            // run: <flags>
            // flags: <flags>
            /* run: <flags> */
        Returns a list of parsed flag strings.
        """
        flags = []
        try:
            if not fp.is_file():
                return flags
            import re
            import shlex
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                for _ in range(30):
                    line = f.readline()
                    if not line:
                        break
                    line_clean = line.strip()
                    match = re.search(r'(?://|/\*)\s*(?:run|flags)\s*:\s*(.+?)(?:\*/|$)', line_clean, re.IGNORECASE)
                    if match:
                        flag_str = match.group(1).strip()
                        if flag_str.lower() in ("cpp", "c", "c++"):
                            continue
                        try:
                            flags.extend(shlex.split(flag_str))
                        except Exception:
                            pass
        except Exception:
            pass
        return flags

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

                    # Check for 'run' shebang
                    if ScriptHandler.is_run_shebang(fp):
                        ext = fp.suffix.lower()
                        if ext in ('.c', '.cpp', '.cc', '.cxx', '.rs', '.java', '.py', '.sh', '.rb', '.js', '.ts', '.go'):
                            return ext

                        # Extensionless file: detect C vs C++
                        try:
                            with open(fp, 'r', encoding='utf-8', errors='ignore') as src_f:
                                lines = [src_f.readline() for _ in range(50)]
                            header_text = "".join(lines)

                            # 1. Explicit directive
                            if re.search(r'(?://|/\*)\s*(?:lang|run)\s*:\s*(?:cpp|c\+\+)', header_text, re.IGNORECASE):
                                return ".cpp"
                            if re.search(r'(?://|/\*)\s*(?:lang|run)\s*:\s*c\b', header_text, re.IGNORECASE):
                                return ".c"

                            # 2. C++ indicators
                            cpp_patterns = [
                                r'#\s*include\s*<iostream>',
                                r'#\s*include\s*<vector>',
                                r'#\s*include\s*<string>',
                                r'#\s*include\s*<algorithm>',
                                r'#\s*include\s*<memory>',
                                r'\bstd::',
                                r'\busing\s+namespace\s+std\b',
                                r'\btemplate\s*<',
                                r'\bclass\s+[A-Za-z_][A-Za-z0-9_]*\s*[{:]',
                            ]
                            for pat in cpp_patterns:
                                if re.search(pat, header_text):
                                    return ".cpp"

                            return ".c"
                        except Exception:
                            return ".c"

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

    @staticmethod
    def _format_script_path(fp: Path) -> str:
        s = str(fp)
        return f"./{s}" if s.startswith("-") else s

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
        
        target = self._format_script_path(fp)
        return self.run_command([prog, "--", target] + self.run_args)

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
        
        target = self._format_script_path(fp)
        return self.run_command([prog, "--", target] + self.run_args)

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
        
        target = self._format_script_path(fp)
        if self.flags.get("debug"):
            return self.run_command([prog, "--inspect-brk", "--", target] + self.run_args)
        else:
            return self.run_command([prog, "--", target] + self.run_args)

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
        
        target = self._format_script_path(fp)
        return self.run_command([prog, "--", target] + self.run_args)

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
        
        target = self._format_script_path(fp)
        return self.run_command([prog, "--", target] + self.run_args)
