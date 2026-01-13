import unittest
import os
import shutil
import time
from pathlib import Path
import subprocess as spc
import sys

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from runner import CompilerRunner

class TestCaching(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_caching_env")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)
        
        # Setup files
        self.main_c = Path("main.c")
        self.lib_c = Path("lib.c")
        self.lib_h = Path("lib.h")
        
        with open(self.lib_h, "w") as f:
            f.write("void hello();")
            
        with open(self.lib_c, "w") as f:
            f.write('#include <stdio.h>\n#include "lib.h"\nvoid hello() { printf("Hello Lib\\n"); }')
            
        with open(self.main_c, "w") as f:
            f.write('#include "lib.h"\nint main() { hello(); return 0; }')

        self.cache_dir = Path(".run_cache")
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)

    def tearDown(self):
        os.chdir(self.original_cwd)
        # Cleanup
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_caching_behavior(self):
        # 1. First Run - Should compile both
        runner = CompilerRunner({"multi": True, "keep": True}) # Keep to check binary, but objs persist anyway
        
        # Capture output to check log messages
        # We can't easily capture Printer output unless we mock it, or redirect stdout
        # But we can check file timestamps in cache
        
        # Run 1
        runner.compile_and_run([str(self.main_c), str(self.lib_c)], multi=True)
        
        # Verify execution
        # Check if output exists (default main.out or ./main.out)
        out_bin = Path("main.exe" if os.name == 'nt' else "./main.out")
        self.assertTrue(out_bin.exists(), "Output binary not created")
        
        if os.name != 'nt':
             # Ensure we have ./ prefix or absolute path for execution
             run_cmd = str(out_bin.resolve())
        else:
             run_cmd = str(out_bin)
        
        # Run binary
        res = spc.run([run_cmd], capture_output=True, text=True)
        self.assertEqual(res.stdout.strip(), "Hello Lib")
        
        # Verify cache interaction
        objs_dir = Path(".run_cache/objs")
        self.assertTrue(objs_dir.exists())
        objs = list(objs_dir.glob("*.o"))
        self.assertEqual(len(objs), 2, "Should have 2 object files in cache")
        
        # Record stats
        obj_mtimes = {o: o.stat().st_mtime for o in objs}
        
        # Wait a bit to ensure mtime diff if we recompile
        time.sleep(1.1)
        
        # 2. Second Run - Source Unchanged - Should skip compilation phase for objects
        # We invoke runner again. 
        # Note: CompilerRunner is re-instantiated usually in main, so cache is reloaded
        runner2 = CompilerRunner({"multi": True, "keep": True})
        runner2.compile_and_run([str(self.main_c), str(self.lib_c)], multi=True)
        
        # Check timestamps - should be UNCHANGED
        for o in objs:
            self.assertEqual(o.stat().st_mtime, obj_mtimes[o], f"Object file {o} should not have been recompiled")
            
        # 3. Third Run - Modify lib.c
        time.sleep(1.1)
        with open(self.lib_c, "a") as f:
            f.write("\n// Check change")
            
        runner3 = CompilerRunner({"multi": True, "keep": True})
        runner3.compile_and_run([str(self.main_c), str(self.lib_c)], multi=True)
        
        # Check timestamps
        # We expect one object file to change (lib.c's object), one to stay same (main.c's object)
        # But names are hashed, hard to map back easily without logic, but we can verify changed count
        
        changed = 0
        for o in objs:
            if o.stat().st_mtime > obj_mtimes[o]:
                changed += 1
                
        self.assertEqual(changed, 1, "Exactly one object file should be recompiled")

if __name__ == '__main__':
    unittest.main()
