#!/usr/bin/env python3

import sys
import shlex
from pathlib import Path

from util.output import Printer, Colors
from util.errors import RunError, ConfigError
from util.update import update
from util.args import args as args_parser
from util.security import SecurityManager
from util.version import version

from runner import CompilerRunner

def main():
    __version__ = version()
    args = args_parser(__version__)
    
    if args.debug:
        import logging
        logging.getLogger("run_kuranne").setLevel(logging.DEBUG)
        Printer.debug("Debug logging enabled")

    # Handle update function
    if args.update:
        update(repo="kuranne/run", current_version=__version__)
        return 0

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
        "preset": args.preset
    }

    try:
        # Init runner
        runner = CompilerRunner(op_flags=operator_flags, extra_flags=args.flags, run_args=args.argument)

        # 1. Check if files provided
        if args.files:
            try:
                runner.compile_and_run(args.files, args.multi)
            finally:
                runner.cleanup()
            return 0

        # 2. Check for -L auto-link mode
        # If -L is present, args.link_auto will be either -1 (if no value provided) or the int value
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
            return 0

        # 3. No files provided -> Check for implicit Cargo Project
        if Path("Cargo.toml").exists():
            # Auto-detect cargo project
            runner.run_cargo_mode(Path("Cargo.toml"))
            return 0

        # 4. No files, No Cargo -> Fallback to Input
        try:
            print(f"{Colors.YELLOW}[ INPUT ] No file given, enter file(s) name: {Colors.RESET}", end="")
            val = input().strip()
            if val: 
                args.files = shlex.split(val)
                runner.compile_and_run(args.files, args.multi)
        except (EOFError, KeyboardInterrupt):
            return 1
        finally:
            runner.cleanup()
            
        return 0

    except RunError as e:
        Printer.error(str(e))
        return 1
    except Exception as e:
        Printer.error(f"Unexpected error: {e}")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)