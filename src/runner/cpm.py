import re
from pathlib import Path
from typing import List, Optional

MAX_SOURCE_SIZE = 10 * 1024 * 1024  # 10 MB limit

class CPM:
    """
    C/C++ Project Manager (CPM)
    Optimized module to handle C/C++ source file analysis, such as 
    locating the entry point (`main`) across multiple files.
    """
    
    @staticmethod
    def get_main_file(sources: List[Path]) -> Optional[Path]:
        """
        Scan a list of C/C++ source files and return the one containing the main function.
        Returns None if no main function is found.
        """
        # Matches standard C/C++ main signatures: int main, void main, int wmain, auto main, etc.
        main_pattern = re.compile(r'(?:int|void|auto)\s+(?:w)?main\s*\(')
        
        for src in sources:
            try:
                with open(src, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(MAX_SOURCE_SIZE)
                    
                # Strip comments to prevent matching commented-out main functions
                content = re.sub(r'//.*?\n|/\*.*?\*/', '', content, flags=re.DOTALL)
                
                if main_pattern.search(content):
                    return src
            except Exception:
                continue
                
        return None
