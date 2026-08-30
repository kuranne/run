import os
import shlex
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from util.config import Config
from util.output import Printer, Colors
from util.errors import ExecutionError, ConfigError
from util.substitutions import VariableSubstitutor

class TaskRunner:
    """
    Handles execution of custom tasks defined in [tasks] of Run.toml.
    """

    @staticmethod
    def is_task(name: str, config: Config) -> bool:
        """
        Check if name matches a defined task.

        Args:
            name (str): Potential task name.
            config (Config): Project configuration.

        Returns:
            bool: True if task exists.
        """
        tasks = config.get_tasks()
        return name in tasks

    @staticmethod
    def run_task(name: str, config: Config, extra_args: List[str], runner_ref: Any) -> bool:
        """
        Execute a defined task.

        Args:
            name (str): Task name.
            config (Config): Project configuration.
            extra_args (List[str]): Additional CLI arguments passed to task.
            runner_ref (Any): BaseRunner reference.

        Returns:
            bool: True if task executed successfully.
        """
        tasks = config.get_tasks()
        task_info = tasks.get(name)
        if not task_info:
            raise ConfigError(f"Task '{name}' not found in Run.toml.")

        task_cmd = ""
        if isinstance(task_info, str):
            task_cmd = task_info
        elif isinstance(task_info, dict):
            task_cmd = task_info.get("command")
            if not task_cmd:
                raise ConfigError(f"Task '{name}' must define a 'command' string.")
            
            # Inject task-specific sandbox flags into runner
            if task_info.get("sandbox"): runner_ref.flags["sandbox"] = True
            if task_info.get("sandbox_net"): runner_ref.flags["sandbox_net"] = True
            if task_info.get("restrict"): runner_ref.flags["restrict"] = True
        else:
            raise ConfigError(f"Task '{name}' has invalid format in Run.toml.")

        out_dir = runner_ref.flags.get("out_dir") if hasattr(runner_ref, "flags") else None
        var_ctx = VariableSubstitutor.build_file_context(out_dir=out_dir)
        task_cmd = VariableSubstitutor.substitute_string(task_cmd, var_ctx)

        Printer.action("TASK", f"{name}: {task_cmd}", Colors.CYAN)
        cmd = shlex.split(task_cmd) + extra_args
        return runner_ref.run_command(cmd)

class ProjectRunner:
    """
    Generic project manifest detector and runner based on [projects] in Run.toml.
    """

    @staticmethod
    def detect_project(start_dir: Path, config: Config) -> Optional[Tuple[str, Dict[str, Any], Path]]:
        """
        Scan starting directory and parent hierarchy for known project manifests.

        Args:
            start_dir (Path): Starting directory path.
            config (Config): Project configuration.

        Returns:
            Optional[Tuple[str, Dict[str, Any], Path]]: (project_type, config_dict, manifest_path).
        """
        projects = config.get_projects()
        current = start_dir.absolute()

        # Check current and up to 3 parent directories
        for _ in range(4):
            for proj_name, proj_cfg in projects.items():
                manifest_file = proj_cfg.get("file")
                if manifest_file:
                    manifest_path = current / manifest_file
                    if manifest_path.exists():
                        return proj_name, proj_cfg, manifest_path
            if current == current.parent:
                break
            current = current.parent

        return None

    @staticmethod
    def _run_step(step_str: str, extra_args: List[str], runner_ref: Any, compiling: bool = False) -> bool:
        """
        Execute a project step, properly splitting compound commands (e.g. '&&' or ';')
        into sequential list-based executions without shell syntax errors.
        """
        if "&&" in step_str:
            subcmds = [s.strip() for s in step_str.split("&&") if s.strip()]
            for idx, sub in enumerate(subcmds):
                flags = extra_args if idx == len(subcmds) - 1 else []
                cmd = shlex.split(sub) + flags
                if not cmd:
                    continue
                if not runner_ref.run_command(cmd, compiling=compiling):
                    return False
            return True
        elif ";" in step_str:
            subcmds = [s.strip() for s in step_str.split(";") if s.strip()]
            for idx, sub in enumerate(subcmds):
                flags = extra_args if idx == len(subcmds) - 1 else []
                cmd = shlex.split(sub) + flags
                if not cmd:
                    continue
                if not runner_ref.run_command(cmd, compiling=compiling):
                    return False
            return True
        else:
            cmd = shlex.split(step_str) + extra_args
            return runner_ref.run_command(cmd, compiling=compiling)

    @staticmethod
    def run_project(project_info: Tuple[str, Dict[str, Any], Path], runner_ref: Any,
                    extra_flags: Optional[List[str]] = None, run_args: Optional[List[str]] = None) -> bool:
        """
        Execute detected project build and run logic.

        Args:
            project_info (Tuple): (project_type, config_dict, manifest_path).
            runner_ref (Any): BaseRunner reference.
            extra_flags (Optional[List[str]]): Compiler/build flags.
            run_args (Optional[List[str]]): Runtime arguments.

        Returns:
            bool: True if execution succeeded.
        """
        proj_name, proj_cfg, manifest_path = project_info
        extra_flags = extra_flags or []
        run_args = run_args or []

        Printer.info(f"Detected project '{proj_name}' via {manifest_path.name}")

        out_dir = runner_ref.flags.get("out_dir") if hasattr(runner_ref, "flags") else None
        var_ctx = VariableSubstitutor.build_file_context(file_path=manifest_path, out_dir=out_dir)

        # Check for two-step build + run
        build_step = proj_cfg.get("build")
        if build_step:
            build_step = VariableSubstitutor.substitute_string(build_step, var_ctx)
        
        run_step = proj_cfg.get("run")
        if run_step:
            run_step = VariableSubstitutor.substitute_string(run_step, var_ctx)

        command_step = proj_cfg.get("command")
        if command_step:
            command_step = VariableSubstitutor.substitute_string(command_step, var_ctx)

        if build_step and run_step:
            # Step 1: Build
            if not ProjectRunner._run_step(build_step, extra_flags, runner_ref, compiling=True):
                return False

            # Step 2: Run
            return ProjectRunner._run_step(run_step, run_args, runner_ref, compiling=False)

        elif command_step:
            return ProjectRunner._run_step(command_step, extra_flags + run_args, runner_ref, compiling=False)
        else:
            raise ConfigError(f"Project '{proj_name}' must define 'command' or both 'build' and 'run'.")

    @staticmethod
    def get_watch_files(manifest_path: Path) -> List[str]:
        """
        Get files to watch for this project.

        Args:
            manifest_path (Path): Path to manifest.

        Returns:
            List[str]: List of file paths to watch.
        """
        watch_list = [str(manifest_path)]
        proj_dir = manifest_path.parent
        src_dir = proj_dir / "src"
        if src_dir.exists() and src_dir.is_dir():
            for p in src_dir.rglob("*"):
                if p.is_file():
                    watch_list.append(str(p))
        return watch_list
