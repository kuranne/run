from pathlib import Path
from typing import List

class CustomLanguageHandler:
    """
    Mixin class handling custom language configurations.
    """
    def _handle_custom_language(self, fp: Path, lang_config: dict, out_name: Path):
        """
        Handle custom language execution based on configuration.

        Args:
            fp (Path): Source file path.
            lang_config (dict): Language configuration dictionary.
            out_name (Path): Output executable path.
        """
        from util.errors import ConfigError
        lang_name = lang_config.get("name", "unknown")
        runner = lang_config.get("runner")
        subcommand = lang_config.get("subcommand")
        lang_type = lang_config.get("type", "interpreter")
        flags = lang_config.get("flags", []) # List
        preset_flags = self.config.get_preset_flags(self.preset, lang_name)
        execute_args = lang_config.get("arguments", [])
        run_cmd = [runner]
        if subcommand:
            run_cmd.extend(subcommand.split())
        
        if not runner:
             raise ConfigError(f"No runner specified for language: {lang_name}")
        
        if lang_type == "interpreter":
            # Run directly like Python, Ruby, etc.

            cmd = run_cmd + flags + self.extra_flags + preset_flags + [str(fp)] + execute_args + self.run_args 

            self.run_command(cmd)
        elif lang_type == "compiler":
            # Compile first, then execute like C/C++
            
            cmd = run_cmd + flags + self.extra_flags + preset_flags + [str(fp), "-o", str(out_name)]
            
            self.run_command(cmd, compiling=True)
            self.output_files.append(out_name)
            self._execute_binary(bin_path=out_name, args=execute_args)
        else:
             raise ConfigError(f"Unknown language type '{lang_type}' for {lang_name}")
