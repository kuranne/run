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
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-F-Wall'])
    parsed = args("1.0.0")
    assert parsed.flags == '-Wall'
    
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-F', '-Wall'])
    parsed = args("1.0.0")
    assert parsed.flags == '-Wall'

    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--flags', '-O3'])
    parsed = args("1.0.0")
    assert parsed.flags == '-O3'

def test_args_argument_parsing(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', 'main.py', '-a', '-v'])
    parsed = args("1.0.0")
    assert parsed.argument == '-v'

    monkeypatch.setattr(sys, 'argv', ['run', 'main.py', '--argument', '--debug'])
    parsed = args("1.0.0")
    assert parsed.argument == '--debug'

    monkeypatch.setattr(sys, 'argv', ['run', 'main.py', '-a--port=8080'])
    parsed = args("1.0.0")
    assert parsed.argument == '--port=8080'

def test_args_force_flag(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-f'])
    parsed = args("1.0.0")
    assert parsed.force is True

    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--force'])
    parsed = args("1.0.0")
    assert parsed.force is True

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

def test_args_posix_double_dash_forwarding(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', 'main.py', '--', '-v', '--port', '8080'])
    parsed = args("1.0.0")
    assert parsed.files == ['main.py']
    assert parsed.argument == '-v --port 8080'

def test_args_posix_double_dash_merge_with_flag(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', 'main.py', '-a', 'initial', '--', 'extra1', 'extra2'])
    parsed = args("1.0.0")
    assert parsed.files == ['main.py']
    assert parsed.argument == 'initial extra1 extra2'

def test_args_jobs_flag(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-j', '8'])
    parsed = args("1.0.0")
    assert parsed.jobs == 8

    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--jobs', '4'])
    parsed = args("1.0.0")
    assert parsed.jobs == 4

def test_args_directory_flag(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-C', 'src/'])
    parsed = args("1.0.0")
    assert parsed.directory == 'src/'

def test_args_clean_flag(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', '--clean'])
    parsed = args("1.0.0")
    assert parsed.clean is True

def test_args_out_dir_aliases(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-O', 'bin/'])
    parsed = args("1.0.0")
    assert parsed.out_dir == 'bin/'

    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-o', 'dist/'])
    parsed = args("1.0.0")
    assert parsed.out_dir == 'dist/'

def test_args_memory_flags(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-M'])
    parsed = args("1.0.0")
    assert parsed.mem is True

    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--mem'])
    parsed = args("1.0.0")
    assert parsed.mem is True

    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--memory'])
    parsed = args("1.0.0")
    assert parsed.mem is True

def test_args_time_and_memory_combined(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-t', '-M'])
    parsed = args("1.0.0")
    assert parsed.time is True
    assert parsed.mem is True

def test_args_stdin_flag_variants(monkeypatch):
    # Standalone -i
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-i'])
    parsed = args("1.0.0")
    assert parsed.stdin == '-'

    # Explicit -i -
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-i', '-'])
    parsed = args("1.0.0")
    assert parsed.stdin == '-'

    # -i with file path
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-i', 'input.txt'])
    parsed = args("1.0.0")
    assert parsed.stdin == 'input.txt'

    # -i combined with -- forwarding
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-i', '--', 'arg1', 'arg2'])
    parsed = args("1.0.0")
    assert parsed.stdin == '-'
    assert parsed.argument == 'arg1 arg2'
