#!/usr/bin/env python3

import sys
import shlex
from pathlib import Path

from util.output import Printer, Colors
from util.errors import RunError, ConfigError
from util.args import args as args_parser
from util.security import SecurityManager
from util.version import version

from runner import CompilerRunner

def main():
    __version__ = version()
    args = args_parser(__version__)
    
    if args.verbose >= 1:
        import logging
        logging.getLogger("run_kuranne").setLevel(logging.DEBUG)
        Printer.debug("Debug logging enabled")

    # Security Check
    try:
        SecurityManager.check_root(allow_root=args.unsafe)
    except ConfigError as e:
        Printer.error(str(e))
        return 1

    # Process operation and flag(s) -> dictionary of it
    operator_flags = {
        "multi" : args.multi,
        "keep" : args.keep,
        "time" : args.time,
        "dry_run": args.dry_run,
        "preset": args.preset,
        "no_cache": args.no_cache,
        "quiet": args.quiet,
        "watch": args.watch,
        "timeout": args.timeout,
        "stdin": args.stdin,
        "env": args.env,
        "out_dir": args.out_dir,
        "compiler": args.compiler
    }

    try:
        def run_once() -> list[str]:
            runner = CompilerRunner(op_flags=operator_flags, extra_flags=args.flags, run_args=args.argument)
            
            # 1. Check if files provided
            if args.files:
                try:
                    runner.compile_and_run(args.files, args.multi)
                finally:
                    runner.cleanup()
                return args.files

            # 2. Check for -L auto-link mode
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

            # 3. No files provided -> Check for implicit Cargo Project
            if Path("Cargo.toml").exists():
                runner.run_cargo_mode(Path("Cargo.toml"))
                return ["Cargo.toml"] + [str(p) for p in Path("src").rglob("*") if p.is_file()]

            # 4. No files, No Cargo -> Fallback to Input
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
            Printer.info("Watch mode enabled. Waiting for file changes...")
            print("-" * 40)
            last_mtimes = {}
            first_run = True
            watch_files = []

            def get_watch_targets(active_files: list[str]) -> set:
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
                        watch_files = run_once() or []
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
                            
                if changed:
                    print("\n" + "-" * 40)
                    Printer.info("File change detected. Restarting...")
                    try:
                        watch_files = run_once() or watch_files
                    except Exception as e:
                        Printer.error(f"Error: {e}")
                    
                    for p in get_watch_targets(watch_files):
                        if p.exists():
                            last_mtimes[str(p)] = p.stat().st_mtime
                            
                time.sleep(1.0)
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