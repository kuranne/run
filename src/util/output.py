class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    CYAN = '\033[96m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    GRAY = '\033[1;30m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

class Printer:
    """
    Utility class for standardized terminal output with colors and tags.
    """
    @staticmethod
    def action(tag: str, message: str, color: str = Colors.GREEN):
        """
        Print an action with a tagged prefix.
        
        Args:
            tag (str): The tag to display (e.g., 'COMPILE', 'RUN').
            message (str): The message content.
            color (str): ANSI color code for the tag.
        """
        # [ TAG ] Message
        print(f"{Colors.BOLD}{color}[ {tag} ]{Colors.RESET} {message}")

    @staticmethod
    def time(seconds: float):
        """
        Print execution time.

        Args:
            seconds (float): Duration in seconds.
        """
        print(f"{Colors.GRAY}  -> Took {seconds:.3f}s{Colors.RESET}")

    @staticmethod
    def error(message: str):
        """
        Print an error message.

        Args:
            message (str): Error description.
        """
        print(f"{Colors.BOLD}{Colors.RED}[ ERROR ]{Colors.RESET} {message}")
    
    @staticmethod
    def info(message: str):
        """
        Print an informational message.

        Args:
            message (str): Info content.
        """
        print(f"{Colors.BOLD}{Colors.CYAN}[ INFO ]{Colors.RESET} {message}")

    @staticmethod
    def warning(message: str):
        """
        Print a warning message.

        Args:
            message (str): Warning content.
        """
        print(f"{Colors.BOLD}{Colors.YELLOW}[ WARN ]{Colors.RESET} {message}")

    @staticmethod
    def separator():
        """Print a visual separator line."""
        print(f"\n{Colors.GRAY}{'-'*30}{Colors.RESET}\n")
