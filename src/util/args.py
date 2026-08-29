from argparse import ArgumentParser
import sys
import shlex
from typing import List

def args(__version__: str):
    """
    Parse command-line arguments and flags for the runner.

    Args:
        __version__ (str): Version string for version display.

    Returns:
        argparse.Namespace: Parsed CLI options.
    """
    parser = ArgumentParser(
        description="Professional Auto Compiler & Runner",
        usage="run [files...] [options] [-- <program-args...>]"
    )
    
    # Positional files
    parser.add_argument("files", nargs="*", help="Source file(s) or task name to run")
    
    # Execution & Runtime group
    exec_group = parser.add_argument_group("Execution & Runtime")
    exec_group.add_argument("-w", "--watch", action="store_true", help="Watch mode: re-compile and run on file change")
    exec_group.add_argument("-f", "--force", action="store_true", help="Force continue on errors without interactive prompts")
    exec_group.add_argument("-d", "--dry-run", action="store_true", help="Simulate execution without running commands")
    exec_group.add_argument("-t", "--time", action="store_true", help="Measure and display execution time")
    exec_group.add_argument("-q", "--quiet", action="store_true", help="Silence compiler output and logs")
    exec_group.add_argument("-v", "--verbose", action="count", default=0, help="Verbose mode (-v for debug, -vv for trace)")
    exec_group.add_argument("-T", "--timeout", type=float, help="Timeout in seconds for execution")
    exec_group.add_argument("-i", "--stdin", type=str, help="Read stdin from file")
    exec_group.add_argument("-e", "--env", action="append", default=[], help="Set environment variable (e.g. PORT=8080)")
    exec_group.add_argument("-a", "--argument", type=str, default="", help="Arguments to pass to the executed program (or use --)")

    # Compilation & Build group
    build_group = parser.add_argument_group("Compilation & Build")
    build_group.add_argument("-m", "--multi", action="store_true", help="Compile multiple source files together")
    build_group.add_argument("-L", "--link-auto", nargs="?", const=-1, type=int, help="Auto find and link C/C++ files (optional depth)")
    build_group.add_argument("-p", "--preset", type=str, help="Configuration preset from Run.toml (e.g. debug, release)")
    build_group.add_argument("-F", "--flags", type=str, default="", help="Compiler or interpreter flags")
    build_group.add_argument("-c", "--compiler", type=str, help="Compiler or interpreter override (e.g. clang++)")
    build_group.add_argument("-O", "-o", "--out-dir", type=str, help="Output directory for compiled binaries")
    build_group.add_argument("-j", "--jobs", type=int, help="Number of parallel compilation worker threads")
    build_group.add_argument("--keep", action="store_true", help="Keep the output binary(s) after execution")
    build_group.add_argument("--no-cache", action="store_true", help="Disable build cache")

    # Project & Utilities group
    util_group = parser.add_argument_group("Project & Utilities")
    util_group.add_argument("-I", "--init", action="store_true", help="Initialize tailored Run.toml for the current project")
    util_group.add_argument("-C", "--directory", type=str, help="Change directory before executing")
    util_group.add_argument("--clean", action="store_true", help="Clear local build cache and exit")
    util_group.add_argument("--unsafe", action="store_true", help="Allow running as root")
    util_group.add_argument("-V", "--version", action="version", version=__version__, help="Check version of the binary")

    # Split trailing arguments after '--'
    cli_argv = sys.argv[1:]
    trailing_args: List[str] = []
    if "--" in cli_argv:
        split_idx = cli_argv.index("--")
        trailing_args = cli_argv[split_idx + 1:]
        cli_argv = cli_argv[:split_idx]

    processed_args = []
    i = 0
    while i < len(cli_argv):
        arg = cli_argv[i]
        
        if arg.startswith("-F") and len(arg) > 2 and not arg.startswith("-F="):
            processed_args.append(f"-F={arg[2:]}")
        elif arg.startswith("-a") and len(arg) > 2 and not arg.startswith("-a="):
            processed_args.append(f"-a={arg[2:]}")
        elif arg in ("-F", "--flags") and i + 1 < len(cli_argv):
            next_arg = cli_argv[i + 1]
            if next_arg.startswith("-"):
                processed_args.append(f"--flags={next_arg}")
                i += 1
            else:
                processed_args.append(arg)
        elif arg in ("-a", "--argument") and i + 1 < len(cli_argv):
            next_arg = cli_argv[i + 1]
            if next_arg.startswith("-"):
                processed_args.append(f"--argument={next_arg}")
                i += 1
            else:
                processed_args.append(arg)
        else:
            processed_args.append(arg)
            
        i += 1

    parsed = parser.parse_args(processed_args)
    if trailing_args:
        trailing_str = " ".join(shlex.quote(a) for a in trailing_args)
        if parsed.argument:
            parsed.argument = f"{parsed.argument} {trailing_str}"
        else:
            parsed.argument = trailing_str

    return parsed