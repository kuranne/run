import unittest
from pathlib import Path
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from src.util.validator import Validator

class TestValidator(unittest.TestCase):
    def test_validate_path_safe(self):
        self.assertTrue(Validator.validate_path(Path("test.c")))
        self.assertTrue(Validator.validate_path(Path("folder/test.c")))

    def test_validate_path_unsafe(self):
        self.assertFalse(Validator.validate_path(Path("test;rm -rf /")))
        self.assertFalse(Validator.validate_path(Path("$(echo pwned)")))
