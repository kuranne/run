import pytest
from pathlib import Path
from util.version import version

def test_version_from_file(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_bytes(b"""
    [project]
    name = "run"
    version = "1.2.3"
    """)

    ver = version(file_path=pyproject)
    assert ver == "1.2.3"

def test_version_missing_file(tmp_path):
    missing_file = tmp_path / "non_existent.toml"
    ver = version(file_path=missing_file)
    assert ver is None or isinstance(ver, str)
