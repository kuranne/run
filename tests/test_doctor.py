import pytest
from util.doctor import Doctor

def test_doctor_check_binary():
    python_info = Doctor.check_binary("python3", "--version")
    assert python_info is not None
    assert "Python" in python_info

    missing = Doctor.check_binary("definitely_nonexistent_binary_xyz123")
    assert missing is None

def test_doctor_diagnose(capsys):
    ret = Doctor.diagnose()
    assert ret == 0
    out = capsys.readouterr().out
    assert "System Toolchain Diagnostics" in out
    assert "Python:" in out
    assert "C / C++:" in out
