import re
from pathlib import Path
from typing import Optional, List, Set, Dict

MAX_SOURCE_SIZE = 10 * 1024 * 1024  # 10 MB limit

class JPM:
    """
    Java Project Manager (JPM)
    Optimized module to handle Java source file analysis, dependency checking, 
    and metadata extraction out of the main execution flow.
    """
    
    @staticmethod
    def get_main_class(java_file: Path) -> Optional[str]:
        """
        Extract the main class name from a Java file.
        Since package management is out of scope for the CLI, we strictly 
        assume the main class matches the filename.
        """
        try:
            return java_file.stem
        except Exception:
            return None

    @staticmethod
    def get_main_file(sources: List[Path]) -> Optional[Path]:
        """
        Scan a list of Java source files and return the one containing the main method.
        Returns None if no main method is found.
        """
        main_pattern = re.compile(r'public\s+static\s+void\s+main\s*\(\s*String')
        
        for src in sources:
            try:
                with open(src, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(MAX_SOURCE_SIZE)
                    
                # Strip comments
                content = re.sub(r'//.*?\n|/\*.*?\*/', '', content, flags=re.DOTALL)
                
                if main_pattern.search(content):
                    return src
            except Exception:
                continue
                
        return None

    @staticmethod
    def record_class_files(directories: set) -> dict:
        """
        Record the modification times of all .class files in given directories.
        """
        state = {}
        for d in directories:
            for p in d.glob("*.class"):
                try:
                    state[p] = p.stat().st_mtime
                except FileNotFoundError:
                    pass
        return state

    @staticmethod
    def get_new_class_files(directories: set, before_state: dict) -> list:
        """
        Find any .class files that were created or modified after before_state.
        """
        new_files = []
        for d in directories:
            for p in d.glob("*.class"):
                try:
                    mtime = p.stat().st_mtime
                    if p not in before_state or mtime > before_state[p]:
                        new_files.append(p)
                except FileNotFoundError:
                    pass
        return new_files
