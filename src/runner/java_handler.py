from pathlib import Path
from typing import List, Optional
import re

class JavaHandler:
    """
    Mixin class handling Java-specific operations.
    """
    def _extract_java_main_class(self, java_file: Path) -> Optional[str]:
        """
        Extract the main class name from a Java file.

        Args:
            java_file (Path): Path to the Java file.

        Returns:
            Optional[str]: Name of the main class, or None if not found.
        """
        try:
            with open(java_file, 'r') as f:
                content = f.read()
                
                # Check for package declaration
                package_name = ""
                package_match = re.search(r'^\s*package\s+([\w.]+)\s*;', content, re.MULTILINE)
                if package_match:
                    package_name = package_match.group(1) + "."

                # Look for public class declaration
                
                # Match: public class ClassName
                match = re.search(r'public\s+class\s+(\w+)', content)
                if match:
                    return package_name + match.group(1)
                # Fallback: try to find any class with main method
                match = re.search(r'class\s+(\w+)\s*\{[^}]*public\s+static\s+void\s+main', content, re.DOTALL)
                if match:
                    return package_name + match.group(1)
        except Exception as e:
            # This is a bit lower level error, maybe just logging is fine, but lets conform
            from util.errors import ExecutionError
            raise ExecutionError(f"Error reading Java file: {e}")
        return None

    def _handle_java_single_file(self, fp: Path):
        """
        Handle single Java file compilation and execution.

        Args:
            fp (Path): Path to the Java source file.
        """
        compiler = self.config.get_runner("java", "javac")
        preset_flags = self.config.get_preset_flags(self.preset, "java")
        
        # Compile the Java file
        cmd = [compiler] + self.extra_flags + preset_flags + [str(fp)]
        
        self.run_command(cmd, compiling=True)
        # If raises exception, we won't reach here
        
        # Extract main class and run
        main_class = self._extract_java_main_class(fp)
        if main_class:
            class_file = fp.with_suffix('.class')
            self.output_files.append(class_file)
            self.run_command(["java", main_class] + self.run_args)
        else:
            from util.errors import ExecutionError
            raise ExecutionError(f"Could not find main class in {fp}")

    def _handle_multi_java(self, sources: List[Path]):
        """
        Handle multi-file Java compilation.

        Args:
            sources (List[Path]): List of Java source files.
        """
        compiler = self.config.get_runner("java", "javac")
        preset_flags = self.config.get_preset_flags(self.preset, "java")
        
        # Compile all Java files
        cmd = [compiler] + self.extra_flags + preset_flags + [str(s) for s in sources]
        
        self.run_command(cmd, compiling=True)
        
        # Extract main class name from the first file
        main_class = self._extract_java_main_class(sources[0])
        if main_class:
            # Add .class files to cleanup
            for src in sources:
                class_file = src.with_suffix('.class')
                self.output_files.append(class_file)
            
            # Run the main class
            cmd = ["java", main_class] + self.run_args
            self.run_command(cmd)
        else:
            from util.errors import ExecutionError
            raise ExecutionError(f"Could not find main class in {sources[0]}")
