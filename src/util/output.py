import logging
import sys

class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    CYAN = '\033[96m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    GRAY = '\033[1;30m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

class TaggedFormatter(logging.Formatter):
    """Custom formatter to replicate [ TAG ] Message style."""
    
    TAGS = {
        logging.DEBUG: ("DEBUG", Colors.GRAY),
        logging.INFO: ("INFO", Colors.CYAN),
        logging.WARNING: ("WARN", Colors.YELLOW),
        logging.ERROR: ("ERROR", Colors.RED),
        logging.CRITICAL: ("CRIT", Colors.RED),
    }

    def format(self, record):
        # Allow custom tag override via extra={'tag': 'MYTAG', 'color': ...}
        tag, color = self.TAGS.get(record.levelno, ("LOG", Colors.RESET))
        
        if hasattr(record, 'tag'):
            tag = record.tag
        if hasattr(record, 'color'):
            color = record.color
            
        message = super().format(record)
        return f"{Colors.BOLD}{color}[ {tag} ]{Colors.RESET} {message}"

# Setup root logger
logger = logging.getLogger("run_kuranne")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(TaggedFormatter())
logger.addHandler(handler)

class Printer:
    """Utility class wrapper for logging."""
    @staticmethod
    def action(tag: str, message: str, color: str = Colors.GREEN):
        """Print an action with a tagged prefix."""
        # We use INFO level but override tag/color
        logger.info(message, extra={'tag': tag, 'color': color})

    @staticmethod
    def time(seconds: float):
        """Print execution time."""
        # Direct print for time to avoid logger format, or we can use a helper
        print(f"{Colors.GRAY}  -> Took {seconds:.3f}s{Colors.RESET}")

    @staticmethod
    def error(message: str):
        """Print an error message."""
        logger.error(message)
    
    @staticmethod
    def info(message: str):
        """Print an informational message."""
        logger.info(message)

    @staticmethod
    def warning(message: str):
        """Print a warning message."""
        logger.warning(message)
        
    @staticmethod
    def debug(message: str):
        """Print debug message (only if level is DEBUG)."""
        logger.debug(message)

    @staticmethod
    def separator():
        """Print a visual separator line."""
        print(f"\n{Colors.GRAY}{'-'*30}{Colors.RESET}\n")
