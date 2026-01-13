#!/usr/bin/env python3

import sys
from argparse import ArgumentParser
import shlex
from pathlib import Path

from util.output import Printer, Colors
from util.errors import RunError, ConfigError, ExecutionError, CompilationError
from util.update import update
from util.security import SecurityManager
from runner import CompilerRunner

def main():
    # Parser
    parser = ArgumentParser(description="Professional Auto Compiler & Runner")
    parser.add_argument("files", nargs="*", help="Files to compile and run")
    
    parser.add_argument("--keep", action="store_true", help="Keep the output binary(s)")
    parser.add_argument("-m", "--multi", action="store_true", help="Compile multi-files")
    parser.add_argument("-t", "--time", action="store_true", help="Time counter for execute binary")
    parser.add_argument("-d", "--dry-run", action="store_true", help="Simulate execution without running commands")
    parser.add_argument("-p", "--preset", type=str, help="Configuration preset (from Run.toml)")
    parser.add_argument("-u", "--update", action="store_true", help="Update run to latest version from GitHub")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--unsafe", action="store_true", help="Allow running as root")
    parser.add_argument("--version", action="store_true", help=f"Versions of binary")
    
    parser.add_argument("-L", "--link-auto", nargs="?", const=-1, type=int, help="Auto find and link C/C++ files. Optional depth arg (default: infinite)")
    parser.add_argument("-f", "--flags", type=str, default="", help='Compiler flags')

    # Process -f-flags without space
    processed_args = []
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg.startswith("-f") and len(arg) > 2:
            processed_args.append("-f")
            processed_args.append(arg[2:])
        else:
            processed_args.append(arg)
        i += 1

    args = parser.parse_args(processed_args)
    
    # Check Versions & Update
    try:
        version_file = Path(__file__).resolve().parent / "version.txt"
        
        with open(version_file, "r") as f:
            __version__ = "".join(f.read().split())
            if args.version:
                Printer.info(f"Currently: {__version__}")
                return 0

            if args.update:
                Printer.info(f"Currently: {__version__}")
                update("kuranne/run", __version__)
                return 0
    
    except FileNotFoundError:
        Printer.warning("Not found version.txt in binary directory, please reinstall run")
        return 1
    
    if args.debug:
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
        "preset": args.preset
    }

    try:
        # Init runner
        runner = CompilerRunner(op_flags=operator_flags, extra_flags=args.flags)

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