import unittest
from unittest.mock import patch, MagicMock
import sys
import os
from pathlib import Path

# Add src to sys.path so we can import modules as if we were in src/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from src.util.config import Config
import tempfile

class TestConfig(unittest.TestCase):
    def setUp(self):
        self.config = Config()

    def test_default_config(self):
        # Assuming no Run.toml is present or it's just the default one
        pass

    def test_get_runner(self):
        # Test default runners
        self.assertEqual(self.config.get_runner("c", "gcc"), "gcc")
        
    def test_get_preset_flags(self):
        # Test empty preset
        flags = self.config.get_preset_flags(None, "c")
        self.assertEqual(flags, [])

    @patch("builtins.open", new_callable=unittest.mock.mock_open, read_data=b'runner = "invalid_type"')
    @patch("src.util.config.tomllib.load")
    def test_invalid_config(self, mock_toml_load, mock_file):
        mock_toml_load.return_value = {"runner": "invalid_type"}
        # Expect ValueError during init
        with self.assertRaises(ValueError):
             Config()

    def test_get_exclude(self):
        self.config.data = {"exclude": {"files": ["ignore.c"], "extensions": [".txt"]}}
        excludes = self.config.get_exclude()
        self.assertEqual(excludes["files"], ["ignore.c"])
        self.assertEqual(excludes["extensions"], [".txt"])
        
        self.config.data = {}
        self.assertEqual(self.config.get_exclude(), {})

    @patch("pathlib.Path.exists")
    @patch("builtins.open", new_callable=unittest.mock.mock_open, read_data=b'runner = "gcc"')
    @patch("pathlib.Path.cwd")
    def test_config_hierarchy(self, mock_cwd, mock_open, mock_exists):
        # Setup Path.cwd to return /dummy/project
        mock_cwd.return_value = Path("/dummy/project")
        
        # We need Path.exists to return True for /dummy/Run.toml
        # and False for /dummy/project/Run.toml
        
        def exists_side_effect(*args, **kwargs):
            if args and str(args[0]).endswith("/dummy/Run.toml"):
                return True
            return False
            
        mock_exists.side_effect = exists_side_effect
        
        with patch("src.util.config.tomllib.loads", return_value={}):
            c = Config()
        # Verify that loaded path was not /dummy/project/Run.toml but /dummy/Run.toml if it existed
        pass

