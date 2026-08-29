import logging
import os
import sys
import difflib
from typing import Optional, List

def should_disable_color() -> bool:
    """Check if color output should be disabled based on environment or flags."""
    return bool(os.getenv("NO_COLOR")) or "--no-color" in sys.argv

class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '' if should_disable_color() else '\033[92m'
    CYAN = '' if should_disable_color() else '\033[96m'
    YELLOW = '' if should_disable_color() else '\033[93m'
    RED = '' if should_disable_color() else '\033[91m'
    GRAY = '' if should_disable_color() else '\033[1;30m'
    RESET = '' if should_disable_color() else '\033[0m'
    BOLD = '' if should_disable_color() else '\033[1m'

    @classmethod
    def disable(cls):
        """Disable all ANSI color codes."""
        cls.GREEN = ''
        cls.CYAN = ''
        cls.YELLOW = ''
        cls.RED = ''
        cls.GRAY = ''
        cls.RESET = ''
        cls.BOLD = ''

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
    def metrics(seconds: Optional[float] = None, memory_bytes: Optional[int] = None):
        """
        Print execution performance metrics (time and/or peak memory).

        Args:
            seconds (Optional[float]): Execution duration in seconds.
            memory_bytes (Optional[int]): Peak memory usage in bytes.
        """
        parts = []
        if seconds is not None:
            parts.append(f"Took {seconds:.3f}s")
        if memory_bytes is not None and memory_bytes > 0:
            mb = memory_bytes / (1024 * 1024)
            if mb >= 1.0:
                parts.append(f"Peak Memory: {mb:.2f} MB")
            else:
                kb = memory_bytes / 1024
                parts.append(f"Peak Memory: {kb:.1f} KB")
        if parts:
            print(f"{Colors.YELLOW}  -> {' | '.join(parts)}{Colors.RESET}")

    @staticmethod
    def time(seconds: float):
        """Print execution time."""
        Printer.metrics(seconds=seconds)

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

    @staticmethod
    def diff(expected: str, actual: str, expected_name: str = "expected"):
        """
        Print a unified diff between expected and actual output.

        Args:
            expected (str): Expected output string.
            actual (str): Actual output string.
            expected_name (str): Label for expected source.
        """
        exp_lines = expected.splitlines(keepends=True)
        act_lines = actual.splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(
            exp_lines, act_lines,
            fromfile=expected_name,
            tofile="actual_output"
        ))
        if diff_lines:
            print(f"\n{Colors.YELLOW}--- Differences ---{Colors.RESET}")
            for line in diff_lines:
                line_clean = line.rstrip("\r\n")
                if line_clean.startswith("+") and not line_clean.startswith("+++"):
                    print(f"{Colors.GREEN}{line_clean}{Colors.RESET}")
                elif line_clean.startswith("-") and not line_clean.startswith("---"):
                    print(f"{Colors.RED}{line_clean}{Colors.RESET}")
                elif line_clean.startswith("@@"):
                    print(f"{Colors.CYAN}{line_clean}{Colors.RESET}")
                else:
                    print(f"{Colors.GRAY}{line_clean}{Colors.RESET}")
            print(f"{Colors.YELLOW}-------------------{Colors.RESET}\n")
