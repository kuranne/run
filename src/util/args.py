from argparse import ArgumentParser
import sys

def args(__version__):
    """
    Parser Argument, recieve argument then parse them, by using argparse lib to manage.
    Return:
        argparse.ArgumentParser
    """

    parser = ArgumentParser(description="Professional Auto Compiler & Runner")
    
    # File name recieve
    parser.add_argument("files", nargs="*", help="Files to compile and run")
    
    # True or False Action
    parser.add_argument("--keep", action="store_true", help="Keep the output binary(s)")
    parser.add_argument("-m", "--multi", action="store_true", help="Compile multi-files")
    parser.add_argument("-t", "--time", action="store_true", help="Time counter for execute binary")
    parser.add_argument("-d", "--dry-run", action="store_true", help="Simulate execution without running commands")
    parser.add_argument("-p", "--preset", type=str, help="Configuration preset (from Run.toml)")
    parser.add_argument("-u", "--update", action="store_true", help="Update run to latest version from GitHub")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--unsafe", action="store_true", help="Allow running as root")

    # Handle version checker
    parser.add_argument("--version", action="version", version=__version__, help=f"Check version of the binary")
    
    # Others
    parser.add_argument("-L", "--link-auto", nargs="?", const=-1, type=int, help="Auto find and link C/C++ files. Optional depth arg (default: infinite)")
    parser.add_argument("-f", "--flags", type=str, default="", help='Compiler flags')
    parser.add_argument("-a", "--argument", type=str, default="", help="Arguments to pass to the executed program")

    # Process args manually before parsing
    processed_args = []
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        
        # Fristly: Parse run -f-Wall -> -f=-Wall
        if arg.startswith("-f") and len(arg) > 2:
            processed_args.append(f"-f={arg[2:]}")
            
        # Secondary: Parse space but start with - ex. run -f "-Wall"
        elif arg == "-f" and i + 1 < len(sys.argv):
            next_arg = sys.argv[i+1]
            
            # If the next arg is -: will force with =
            if next_arg.startswith("-"):
                processed_args.append(f"-f={next_arg}")
                i += 1
            else:
                processed_args.append(arg)
                
        else:
            processed_args.append(arg)
            
        i += 1

    return parser.parse_args(processed_args)