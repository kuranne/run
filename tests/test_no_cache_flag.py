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
        cache_file = cache_dir / "cache.json"
        
        # Cache file should exist for single-file compilation
        self.assertTrue(cache_file.exists(), f"Cache file should exist after normal run (cache_dir={cache_dir}, exists={cache_dir.exists()})")
        
        # Get mtime of cache file
        old_mtime = cache_file.stat().st_mtime
        
        # 2. Modify source
        import time
        time.sleep(1.1)
        self.source_file.write_text('#include <stdio.h>\nint main() { printf("Hello Modified"); return 0; }')
        
        # 3. Run with --no-cache
        cmd = [self.python_exec, str(self.run_script), str(self.source_file), "--no-cache"]
        res = spc.run(cmd, capture_output=True, text=True)
        self.assertIn("Hello Modified", res.stdout)
        
        # Cache file should NOT be updated when running with --no-cache
        new_cache_file = cache_dir / "cache.json"
        if new_cache_file.exists():
            new_mtime = new_cache_file.stat().st_mtime
            # Cache file should not be updated (or at least we didn't use cache)
            # Actually with --no-cache, we ignore the cache completely, so it shouldn't change
            self.assertEqual(new_mtime, old_mtime, "Cache file should not be modified by --no-cache run")

if __name__ == '__main__':
    unittest.main()
