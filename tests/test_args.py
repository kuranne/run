import pytest
import sys
from util.args import args

def test_args_basic(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-m', '-p', 'debug', '-t'])
    parsed = args("1.0.0")
    assert parsed.files == ['main.c']
    assert parsed.multi == True
    assert parsed.preset == 'debug'
    assert parsed.time == True

def test_args_flags_parsing(monkeypatch):
    # Test -f-Wall
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-f-Wall'])
    parsed = args("1.0.0")
    assert parsed.flags == '-Wall'
    
    # Test -f "-Wall"
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-f', '-Wall'])
    parsed = args("1.0.0")
    assert parsed.flags == '-Wall'
    
def test_args_link_auto(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', '-L'])
    parsed = args("1.0.0")
    assert parsed.link_auto == -1
    
    monkeypatch.setattr(sys, 'argv', ['run', '-L', '2'])
    parsed = args("1.0.0")
    assert parsed.link_auto == 2

def test_args_verbose(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', '-v'])
    parsed = args("1.0.0")
    assert parsed.verbose == 1
    
    monkeypatch.setattr(sys, 'argv', ['run', '-vv'])
    parsed = args("1.0.0")
    assert parsed.verbose == 2
