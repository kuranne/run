import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import sys
import os

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from runner.core import CompilerRunner
from util.errors import ExecutionError, CompilationError

class TestCompilerRunner(unittest.TestCase):
    def setUp(self):
        self.runner = CompilerRunner(op_flags={"dry_run": True, "time": False, "keep": False})

    def test_get_executable_path_posix(self):
        self.runner.is_posix = True
        path = Path("test.c")
        exec_path = self.runner.get_executable_path(path)
        # Path("./test.out") normalizes to "test.out"
        self.assertEqual(str(exec_path), "test.out")

    def test_get_executable_path_windows(self):
        self.runner.is_posix = False
        path = Path("test.c")
        exec_path = self.runner.get_executable_path(path)
        self.assertEqual(str(exec_path), "test.exe")

    @patch("subprocess.run")
    def test_run_command_dry_run(self, mock_run):
        # Should not call subprocess.run because dry_run is True
        result = self.runner.run_command(["echo", "hello"])
        self.assertTrue(result)
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_run_command_real(self, mock_run):
        self.runner.dry_run = False
        mock_run.return_value.returncode = 0
        
        result = self.runner.run_command(["echo", "hello"])
        self.assertTrue(result)
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_run_command_failure(self, mock_run):
        self.runner.dry_run = False
        mock_run.return_value.returncode = 1
        
        with self.assertRaises(ExecutionError):
            self.runner.run_command(["false"])

    @patch("builtins.open")
    @patch("pathlib.Path.is_file")
    def test_detect_language_shebang(self, mock_is_file, mock_open):
        mock_is_file.return_value = True
        
        # Mock file content
        mock_file = MagicMock()
        mock_file.__enter__.return_value = mock_file
        mock_file.readline.return_value = "#!/usr/bin/env python3"
        mock_open.return_value = mock_file
        
        path = Path("script_no_ext")
        # Ensure suffix is empty
        # Mocking run_command to verify it detects python
        with patch.object(self.runner, 'run_command') as mock_run_cmd:
             # run_command will be called with python explicitly
             # _get_python_executable is called
             with patch.object(self.runner, '_get_python_executable', return_value="python"):
                 self.runner._handle_single_file(path)
                 mock_run_cmd.assert_called_with(["python", "script_no_ext"])

    @patch("subprocess.run")
    def test_multi_compile_c_link_flags(self, mock_run):
        # Test specifically focusing on _handle_multi_c_family
        self.runner.preset = "build"
        self.runner.extra_flags = []
        self.runner.dry_run = False # Ensure subprocess.run is called
        mock_run.return_value.returncode = 0
        
        # Mock config
        self.runner.config.get_preset_flags = MagicMock(return_value=["-LinkFlag"])
        self.runner.config.get_runner = MagicMock(return_value="gcc")
        
        # Mock compiled object return
        # We patch the class method because it might be picked up differently in threads
        # But patching instance is cleaner
        with patch.object(self.runner, "_compile_object_file", return_value=Path("test.o")):
             # Mock get_executable_path
             self.runner.get_executable_path = MagicMock(return_value=Path("out.exe"))
             self.runner._execute_binary = MagicMock()
             
             sources = [Path("main.c"), Path("lib.c")]
             
             # We rely on real ThreadPoolExecutor here, assuming mock is thread-safe for reading return_value
             self.runner._handle_multi_c_family(sources, sources)
             
             # Check calls to run_command
             # The last call should be the link command
             # Expected: gcc test.o test.o -o out.exe -LinkFlag
             
             if not mock_run.call_args_list:
                 self.fail("subprocess.run was not called")
                 
             link_call = mock_run.call_args_list[-1]
             args = link_call[0][0]
             self.assertIn("-LinkFlag", args, "Preset flags missing from link command")
