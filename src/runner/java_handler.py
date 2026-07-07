from pathlib import Path
from typing import List
from runner.jpm import JPM
from util.errors import ExecutionError

class JavaHandler:
    """
    Mixin class handling Java-specific operations.
    """
    
    def _handle_java_single_file(self, fp: Path):
        """
        Handle single Java file compilation and execution.
        """
        compiler = self.config.get_runner("java", "javac")
        preset_flags = self.config.get_preset_flags(self.preset, "java")
        
        # Record state of .class files before compilation
        parent_dir = set([fp.parent])
        before_state = JPM.record_class_files(parent_dir)

        # Compile the Java file
        cmd = [compiler] + self.extra_flags + preset_flags + [str(fp)]
        self.run_command(cmd, compiling=True)
        
        # Extract main class and run
        main_class = JPM.get_main_class(fp)
        if not main_class:
            raise ExecutionError(f"Could not extract main class from {fp}")

        # Track newly created or modified .class files for cleanup
        self.output_files.extend(JPM.get_new_class_files(parent_dir, before_state))

        # Execute
        self.run_command(["java", main_class] + self.run_args)


    def _handle_multi_java(self, sources: List[Path]):
        """
        Handle multi-file Java compilation.
        """
        compiler = self.config.get_runner("java", "javac")
        preset_flags = self.config.get_preset_flags(self.preset, "java")
        
        # Record class files state across all involved directories
        parent_dirs = set(src.parent for src in sources)
        before_state = JPM.record_class_files(parent_dirs)

        # Compile all Java files
        cmd = [compiler] + self.extra_flags + preset_flags + [str(s) for s in sources]
        self.run_command(cmd, compiling=True)
        
        # Extract main class name from the first file
        main_class = JPM.get_main_class(sources[0])
        if not main_class:
            raise ExecutionError(f"Could not extract main class from {sources[0]}")
            
        # Track new or modified .class files for cleanup
        self.output_files.extend(JPM.get_new_class_files(parent_dirs, before_state))
        
        # Run the main class
        self.run_command(["java", main_class] + self.run_args)
