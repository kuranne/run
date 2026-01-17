import unittest
import os
import shutil
import sys
from pathlib import Path
import subprocess as spc

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

class TestNoCacheFlag(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("test_nocache_env")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)
        
        # Create a dummy source file
        self.source_file = Path("main.c")
        self.source_file.write_text('#include <stdio.h>\nint main() { printf("Hello"); return 0; }')

        # Path to main.py
        self.run_script = Path(self.original_cwd) / "src/main.py"
        self.python_exec = sys.executable

    def tearDown(self):
        os.chdir(self.original_cwd)
        # Cleanup
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_no_cache_flag_prevents_cache_creation(self):
        # Ensure no cache exists initially
        cache_dir = Path(".run_cache")
        self.assertFalse(cache_dir.exists())
        
        # Run with --no-cache
        cmd = [self.python_exec, str(self.run_script), str(self.source_file), "--no-cache"]
        res = spc.run(cmd, capture_output=True, text=True)
        
        self.assertEqual(res.returncode, 0, f"Run failed: {res.stderr}")
        self.assertIn("Hello", res.stdout)
        
        # Assert .run_cache was NOT created
        self.assertFalse(cache_dir.exists(), ".run_cache should not exist after running with --no-cache")
        
        # Assert object files matching source are cleaned up
        obj = self.source_file.with_suffix(".o")
        self.assertFalse(obj.exists(), "Local object file should be cleaned up")

    def test_no_cache_ignores_existing_cache(self):
        # 1. Run normally to populate cache
        cmd = [self.python_exec, str(self.run_script), str(self.source_file)]
        res = spc.run(cmd, capture_output=True, text=True)
        
        if res.returncode != 0:
            print("Run 1 failed:", res.stderr)
        
        cache_dir = Path(".run_cache")
        if not cache_dir.exists():
            print("Run 1 Output:\n", res.stdout)
            print("Run 1 Error:\n", res.stderr)
            
        self.assertTrue(cache_dir.exists())
        
        # Get mtime of object file in cache
        objs = list(cache_dir.glob("objs/*.o"))
        self.assertTrue(len(objs) > 0)
        old_mtime = objs[0].stat().st_mtime
        
        # 2. Modify source
        import time
        time.sleep(1.1)
        self.source_file.write_text('#include <stdio.h>\nint main() { printf("Hello Modified"); return 0; }')
        
        # 3. Run with --no-cache
        cmd = [self.python_exec, str(self.run_script), str(self.source_file), "--no-cache"]
        res = spc.run(cmd, capture_output=True, text=True)
        self.assertIn("Hello Modified", res.stdout)
        
        # Assert cache object NOT updated (or at least we didn't use it)
        # Actually, since we bypass cache, the cache dir might remain stale. 
        # But crucially, we verified "Hello Modified" ran, so it definitely recompiled.
        # And since we didn't update cache, the object in cache should match OLD source?
        # Unless we inadvertently updated it? The logic says if cache=None, we don't call update_cache.
        # So check mtime of cached object should be same as before?
        
        new_objs = list(cache_dir.glob("objs/*.o"))
        if new_objs: # It's possible cache key changed if path hash changed? No, path is same.
            # But wait, we recompiled. Did we touch cache object?
            # c_family_handler: if cache matches, return. If not, compile.
            # But with --no-cache, cache is None. So we compile to LOCAL .o file.
            # The cached .o file in .run_cache/objs should be UNTOUCHED.
            self.assertEqual(new_objs[0].stat().st_mtime, old_mtime, "Cached object should not be modified by --no-cache run")

if __name__ == '__main__':
    unittest.main()
