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
