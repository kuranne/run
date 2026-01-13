import unittest
from pathlib import Path
import os
import sys

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from src.runner.core import CompilerRunner
from src.util.output import Printer
import tempfile
import shutil
import tempfile
import shutil

class TestIntegration(unittest.TestCase):
    def setUp(self):
        self.runner = CompilerRunner(op_flags={"dry_run": False, "time": False, "keep": False})
        self.test_dir = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.test_dir)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.test_dir)

    def test_run_python_script(self):
        script_name = "hello.py"
        with open(script_name, "w") as f:
            f.write("print('Hello Integration')")
        
        # We can't easily capture output here without redirecting stdout/stderr at system level
        # For now, just ensure it runs without error
        try:
            self.runner.compile_and_run([script_name])
        except Exception as e:
            self.fail(f"Integration run failed: {e}")

    def test_compile_run_c(self):
        # Skip if no gcc
        import shutil
        if not shutil.which("gcc"):
            self.skipTest("gcc not found")

        source_name = "test.c"
        with open(source_name, "w") as f:
            f.write('#include <stdio.h>\nint main() { printf("Hello C"); return 0; }')
        
        try:
            self.runner.compile_and_run([source_name])
            self.runner.cleanup()
            
            # Check cleanup happened (no executable left, unless verification failed or kept)
            # By default keep=False, so executable should be gone
            # Note: The runner keeps output_files list.
            if self.runner.is_posix:
                self.assertFalse(os.path.exists("test.out"))
            else:
                self.assertFalse(os.path.exists("test.exe"))
                
        except Exception as e:
            self.fail(f"C integration failed: {e}")
