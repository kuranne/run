#!/usr/bin/env python3

import sys
import shlex
from pathlib import Path

from util.output import Printer, Colors
from util.errors import RunError, ConfigError
from util.args import args as args_parser
from util.security import SecurityManager
from util.version import version
from util.init_config import ConfigInitializer

from runner import CompilerRunner, TaskRunner, ProjectRunner

def main():
    __version__ = version()
    args = args_parser(__version__)
    
    # Handle --no-color
    if args.no_color:
        Colors.disable()

    # Handle internal completion helper (--_complete <type>)
    if hasattr(args, "internal_complete") and args.internal_complete:
        from util.completion import CompletionGenerator
        from util.config import Config
        cfg = Config()
        output = CompletionGenerator.handle_internal_complete(args.internal_complete, cfg)
        if output:
            print(output)
        return 0

    # Handle --completion [shell]
    if hasattr(args, "completion") and args.completion is not None:
        from util.completion import CompletionGenerator
        shell = args.completion.lower().strip()
        if not shell:
            print(CompletionGenerator.get_install_instructions())
            return 0
        script = CompletionGenerator.generate(shell)
        if script:
            print(script, end="" if script.endswith("\n") else "\n")
            return 0
        else:
            Printer.error(f"Unsupported shell '{shell}'. Supported: zsh, bash, fish, powershell")
            return 1

    # Handle --doctor
    if args.doctor:
        from util.doctor import Doctor
        return Doctor.diagnose()

    if args.verbose >= 1:
        import logging
        logging.getLogger("run_kuranne").setLevel(logging.DEBUG)
        Printer.debug("Debug logging enabled")

    import os

    # Handle --directory / --cwd
    if args.directory:
        target_dir = Path(args.directory)
        if not target_dir.is_dir():
            Printer.error(f"Specified directory does not exist: {args.directory}")
            return 1
        os.chdir(target_dir)

    # Handle --clean
    if args.clean:
        from util.cache import CacheManager
        CacheManager().clear()
        Printer.action("CLEAN", "Build cache cleared successfully.", Colors.GREEN)
        return 0

    # Handle --new
    if args.new:
        from util.template_manager import TemplateManager
        from util.config import Config
        cfg = Config()
        success = TemplateManager.generate(args.new, template_name=args.template, config=cfg, force=args.force)
        return 0 if success else 1

    # Handle --init
    if args.init:
        success = ConfigInitializer.init_config(Path("."))
        return 0 if success else 1

    # Security Check
    try:
        SecurityManager.check_root(allow_root=args.unsafe)
    except ConfigError as e:
        Printer.error(str(e))
        return 1

    # Process operation and flag(s) -> dictionary of it
    operator_flags = {
        "multi" : args.multi,
        "keep" : args.keep or args.build_only,
        "build_only": args.build_only,
        "time" : args.time,
        "memory" : args.mem,
        "dry_run": args.dry_run,
        "preset": args.preset,
        "no_cache": args.no_cache,
        "quiet": args.quiet,
        "watch": args.watch,
        "force": args.force,
        "jobs": args.jobs,
        "expect": args.expect,
        "test_dir": args.test_dir,
        "debug": args.debug,
        "gdb": args.gdb,
        "lldb": args.lldb,
        "valgrind": args.valgrind,
        "asan": args.asan,
        "tsan": args.tsan,
        "sanitize": args.sanitize,
        "timeout": args.timeout,
        "stdin": args.stdin,
        "env": args.env,
        "out_dir": args.out_dir,
        "compiler": args.compiler,
        "restrict": args.restrict,
        "sandbox": args.sandbox,
        "sandbox_net": args.sandbox_net
    }

    try:
        def check_user_continue(timeout: float = 1.0) -> bool:
            """
            Check non-blockingly if user pressed 'c' or enter to trigger a rebuild.

            Args:
                timeout (float): Max seconds to wait for input.

            Returns:
                bool: True if user triggered a continue action.
            """
            if not sys.stdin.isatty():
                import time
                time.sleep(timeout)
                return False

            if sys.platform != "win32":
                import select
                rlist, _, _ = select.select([sys.stdin], [], [], timeout)
                if rlist:
                    line = sys.stdin.readline().strip().lower()
                    return line in ("c", "continue", "")
                return False
            else:
                import msvcrt, time
                start = time.time()
                while time.time() - start < timeout:
                    if msvcrt.kbhit():
                        ch = msvcrt.getch().decode("utf-8", errors="ignore").strip().lower()
                        return ch in ("c", "\r", "\n", "")
                    time.sleep(0.05)
                return False

        def run_once() -> list[str]:
            runner = CompilerRunner(op_flags=operator_flags, extra_flags=args.flags, run_args=args.argument)
            
            # Check if --test-dir is provided
            if args.test_dir:
                from runner.test_runner import TestcasesRunner
                if not args.files:
                    raise ConfigError("A source file must be specified with --test-dir (e.g. run solution.cpp --test-dir ./tests/)")
                target = Path(args.files[0])
                try:
                    success = TestcasesRunner.run_tests(runner, Path(args.test_dir), target)
                    if not success and not args.watch:
                        sys.exit(1)
                finally:
                    runner.cleanup()
                return args.files

            # 1. Check if first argument is a defined Task from [tasks]
            if args.files and TaskRunner.is_task(args.files[0], runner.config):
                task_name = args.files[0]
                task_extra_args = args.files[1:]
                TaskRunner.run_task(task_name, runner.config, task_extra_args, runner)
                return [str(p) for p in Path(".").glob("*") if p.is_file()]

            # 2. Check if files provided
            if args.files:
                try:
                    runner.compile_and_run(args.files, args.multi)
                finally:
                    runner.cleanup()
                return args.files

            # 3. Check for -L auto-link mode
            if args.link_auto is not None:
                depth = args.link_auto if args.link_auto != -1 else None
                src_files = runner.find_source_files(Path("."), max_depth=depth)
                if not src_files:
                    raise ConfigError(f"No supported source files found via -L auto-search (depth={depth}).")
                Printer.info(f"Auto-found {len(src_files)} source files: {src_files}")
                try:
                    runner.compile_and_run(src_files, multi=True)
                finally:
                    runner.cleanup()
                return src_files

            # 4. No files provided -> Check for detected project manifest (Cargo, Go, CMake, Zig, etc.)
            detected = ProjectRunner.detect_project(Path("."), runner.config)
            if detected:
                ProjectRunner.run_project(detected, runner, extra_flags=runner.extra_flags, run_args=runner.run_args)
                return ProjectRunner.get_watch_files(detected[2])

            # 5. No files, No Project -> Fallback to Input
            print(f"{Colors.YELLOW}[ INPUT ] No file given, enter file(s) name: {Colors.RESET}", end="")
            val = input().strip()
            if val: 
                args.files = shlex.split(val)
                try:
                    runner.compile_and_run(args.files, args.multi)
                finally:
                    runner.cleanup()
                return args.files
            
            return []

        if args.watch:
            import time
            Printer.info("Watch mode enabled. Waiting for file changes (press 'c' to retry)...")
            print("-" * 40)
            last_mtimes = {}
            first_run = True
            watch_files = list(args.files) if args.files else []

            def get_watch_targets(active_files: list[str]) -> set:
                """Resolve target files and headers to monitor for changes."""
                targets = set(Path(f) for f in active_files if f)
                for cfg_name in ("Run.toml", "Cargo.toml"):
                    cfg = Path(cfg_name)
                    if cfg.exists():
                        targets.add(cfg)
                for f in list(targets):
                    if f.exists() and f.is_file():
                        for h in f.parent.glob("*.h"):
                            targets.add(h)
                        for h in f.parent.glob("*.hpp"):
                            targets.add(h)
                return targets
            
            while True:
                if first_run:
                    try:
                        res = run_once()
                        if res:
                            watch_files = res
                    except Exception as e:
                        Printer.error(f"Error: {e}")
                    
                    for p in get_watch_targets(watch_files):
                        if p.exists():
                            last_mtimes[str(p)] = p.stat().st_mtime
                    first_run = False
                    continue
                
                changed = False
                targets = get_watch_targets(watch_files)
                for p in targets:
                    if p.exists():
                        mtime = p.stat().st_mtime
                        if str(p) not in last_mtimes or last_mtimes[str(p)] < mtime:
                            changed = True
                            last_mtimes[str(p)] = mtime
                            
                if not changed:
                    if check_user_continue(1.0):
                        changed = True

                if changed:
                    print("\n" + "-" * 40)
                    Printer.info("Restarting...")
                    try:
                        res = run_once()
                        if res:
                            watch_files = res
                    except Exception as e:
                        Printer.error(f"Error: {e}")
                    
                    for p in get_watch_targets(watch_files):
                        if p.exists():
                            last_mtimes[str(p)] = p.stat().st_mtime
            return 0
        else:
            run_once()
            return 0
    except (EOFError, KeyboardInterrupt):
        return 0
    except RunError as e:
        Printer.error(str(e))
        return 1
    except Exception as e:
        Printer.error(f"Unexpected error: {e}")
        if args.verbose >= 2:
            import traceback
            traceback.print_exc()
        return 1
    
if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)