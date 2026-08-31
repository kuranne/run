from pathlib import Path
from typing import List, Optional, Dict, Any
import shlex
from util.errors import ConfigError
from util.substitutions import VariableSubstitutor
from .handler_interface import LanguageHandler, ExecutionContext

class CustomLanguageHandler(LanguageHandler):
    """
    Handler for user-defined languages configured via Run.toml.
    """

    def can_handle(self, path: Path, config: Any) -> bool:
        """Check if file extension is configured in Run.toml."""
        ext = path.suffix.lower()
        if hasattr(config, "get_language_by_extension"):
            return config.get_language_by_extension(ext) is not None
        return False

    def run_single(self, fp: Path, ctx: ExecutionContext) -> None:
        """Run a single custom language file."""
        ext = fp.suffix.lower()
        lang_config = ctx.config.get_language_by_extension(ext)
        if not lang_config:
            raise ConfigError(f"No custom configuration found for {fp}")
        out_name = ctx.get_executable_path(fp)
        self._execute_custom(fp, lang_config, out_name, ctx)

    def run_multi(self, paths: List[Path], ctx: ExecutionContext) -> None:
        """Run multiple files for custom language."""
        if not paths:
            return
        ext = paths[0].suffix.lower()
        lang_config = ctx.config.get_language_by_extension(ext)
        if not lang_config:
            raise ConfigError(f"No custom configuration found for {paths[0]}")
        out_name = ctx.get_executable_path(paths[0])
        self._execute_custom_multi(paths, lang_config, out_name, ctx)

    def _execute_custom(self, fp: Path, lang_config: dict, out_name: Path, ctx: ExecutionContext):
        """Execute single file based on config."""
        lang_name = lang_config.get("name", "unknown")
        runner = lang_config.get("runner")
        if not runner:
            raise ConfigError(f"No runner specified for language: {lang_name}")

        var_ctx = VariableSubstitutor.build_file_context(file_path=fp, out_path=out_name, out_dir=ctx.flags.get("out_dir") if ctx.flags else None)
        runner = VariableSubstitutor.substitute_string(runner, var_ctx)
        subcommand = lang_config.get("subcommand")
        if subcommand:
            subcommand = VariableSubstitutor.substitute_string(subcommand, var_ctx)

        lang_type = lang_config.get("type", "interpreter")
        flags = VariableSubstitutor.substitute_list(lang_config.get("flags", []), var_ctx)
        preset_flags = ctx.config.get_preset_flags(ctx.preset, lang_name) if ctx.config else []
        execute_args = VariableSubstitutor.substitute_list(lang_config.get("arguments", []), var_ctx)
        
        run_cmd = [runner]
        if subcommand:
            run_cmd.extend(shlex.split(subcommand))

        if lang_type == "interpreter":
            cmd = run_cmd + flags + ctx.extra_flags + preset_flags + [str(fp)] + execute_args + ctx.run_args
            return ctx.run_command(cmd)
        elif lang_type == "compiler":
            cmd = run_cmd + flags + ctx.extra_flags + preset_flags + [str(fp), "-o", str(out_name)]
            if not ctx.run_command(cmd, compiling=True):
                return False
            ctx.output_files.append(out_name)
            return ctx.execute_binary(out_name, args=execute_args)
        else:
            raise ConfigError(f"Unknown language type '{lang_type}' for {lang_name}")

    def _execute_custom_multi(self, paths: List[Path], lang_config: dict, out_name: Path, ctx: ExecutionContext):
        """Execute multiple files based on config."""
        lang_name = lang_config.get("name", "unknown")
        runner = lang_config.get("runner")
        if not runner:
            raise ConfigError(f"No runner specified for language: {lang_name}")

        var_ctx = VariableSubstitutor.build_file_context(file_path=paths[0] if paths else None, out_path=out_name, out_dir=ctx.flags.get("out_dir") if ctx.flags else None)
        runner = VariableSubstitutor.substitute_string(runner, var_ctx)
        subcommand = lang_config.get("subcommand")
        if subcommand:
            subcommand = VariableSubstitutor.substitute_string(subcommand, var_ctx)

        lang_type = lang_config.get("type", "interpreter")
        flags = VariableSubstitutor.substitute_list(lang_config.get("flags", []), var_ctx)
        preset_flags = ctx.config.get_preset_flags(ctx.preset, lang_name) if ctx.config else []
        execute_args = VariableSubstitutor.substitute_list(lang_config.get("arguments", []), var_ctx)

        run_cmd = [runner]
        if subcommand:
            run_cmd.extend(shlex.split(subcommand))

        if lang_type == "compiler":
            cmd = run_cmd + flags + ctx.extra_flags + preset_flags + [str(p) for p in paths] + ["-o", str(out_name)]
            if not ctx.run_command(cmd, compiling=True):
                return False
            ctx.output_files.append(out_name)
            return ctx.execute_binary(out_name, args=execute_args)
        else:
            cmd = run_cmd + flags + ctx.extra_flags + preset_flags + [str(p) for p in paths] + execute_args + ctx.run_args
            return ctx.run_command(cmd)

    def _handle_custom_language(self, fp: Path, lang_config: dict, out_name: Path):
        """Legacy helper for direct invocation."""
        ctx = ExecutionContext(
            flags=getattr(self, "flags", {}),
            extra_flags=getattr(self, "extra_flags", []),
            run_args=getattr(self, "run_args", []),
            preset=getattr(self, "preset", None),
            config=getattr(self, "config", None),
            cache=getattr(self, "cache", None),
            output_files=getattr(self, "output_files", []),
            is_posix=getattr(self, "is_posix", True),
            runner_ref=self
        )
        return self._execute_custom(fp, lang_config, out_name, ctx)
