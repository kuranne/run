import unittest
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from util.security import SecurityManager
from util.errors import ConfigError

class TestSecurityManager(unittest.TestCase):
    
    @patch("os.geteuid", return_value=0)
    def test_check_root_block(self, mock_geteuid):
        # Should raise ConfigError if root and allow_root=False
        with self.assertRaises(ConfigError):
            SecurityManager.check_root(allow_root=False)

    @patch("os.geteuid", return_value=0)
    def test_check_root_allow(self, mock_geteuid):
        # Should NOT raise if allow_root=True
        try:
            SecurityManager.check_root(allow_root=True)
        except ConfigError:
            self.fail("Should not raise ConfigError when allow_root=True")

    @patch("os.geteuid", return_value=1000)
    def test_check_root_safe(self, mock_geteuid):
        # Non-zero uid is safe
        SecurityManager.check_root(allow_root=False)

    def test_sanitize_env(self):
        with patch.dict(os.environ, {"LD_PRELOAD": "/evil.so", "PATH": "/bin"}):
            env = SecurityManager.sanitize_execution_env()
            self.assertNotIn("LD_PRELOAD", env)
            self.assertIn("PATH", env)
