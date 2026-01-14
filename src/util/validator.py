from pathlib import Path
from typing import List

class Validator:
    """
    Utility class for input validation and sanitization.
    """
    
    @staticmethod
    def validate_path(path: Path) -> bool:
        """
        Validate that the path is safe (e.g. no traversal if restricted).
        For this runner, we mostly check if it's not suspicious, but since it's a runner,
        users might run things anywhere. 
        Mainly we want to prevent command injection from file names if they are blindly shell executed.
        
        Args:
            path (Path): Path to check.

        Returns:
            bool: True if safe.
        """
        # A simple check: ensure no shell control characters in the filename if strictly needed
        # But 'shlex.quote' or passing list to subprocess handles execution safety.
        # This validator is more about business logic rules if any.
        
        unsafe_chars = [';', '&', '|', '`', '$', '(', ')']
        name = path.name
        for char in unsafe_chars:
            if char in name:
                # While shlex handles this, it's weird to have source files with these chars
                return False
        return True

    @staticmethod
    def validate_flags(flags: List[str]) -> bool:
        """
        Check for obviously dangerous flags if necessary.
        """
        # This is hard to generalize for all compilers.
        return True
