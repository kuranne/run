from pathlib import Path
from typing import List, Union

class Validator:
    """
    Utility class for input validation and sanitization.
    """
    
    @staticmethod
    def validate_path(path: Union[Path, str]) -> bool:
        """
        Validate that the path is safe from traversal, control characters, and dangerous shell metacharacters.
        
        Args:
            path (Union[Path, str]): Path to check.

        Returns:
            bool: True if safe, False if suspicious.
        """
        path_str = str(path)
        path_obj = Path(path)

        # 1. Reject control characters (ASCII < 32: null bytes, newlines, carriage returns, etc.)
        if any(ord(c) < 32 for c in path_str):
            return False

        # 2. Reject path traversal sequences
        if ".." in path_obj.parts:
            return False

        # 3. Reject dangerous shell metacharacters across full path
        unsafe_chars = {';', '&', '|', '`', '$', '(', ')', '<', '>'}
        if any(c in unsafe_chars for c in path_str):
            return False

        return True

    @staticmethod
    def validate_flags(flags: List[str]) -> bool:
        """
        Check for obviously dangerous flags if necessary.
        """
        # This is hard to generalize for all compilers.
        return True
