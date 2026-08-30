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
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--flags=-Wall'])
    parsed = args("1.0.0")
    assert parsed.flags == '-Wall'
    
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--flags', '-O3'])
    parsed = args("1.0.0")
    assert parsed.flags == '-O3'

def test_args_argument_parsing(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', 'main.py', '--argument', '--debug'])
    parsed = args("1.0.0")
    assert parsed.argument == '--debug'

    monkeypatch.setattr(sys, 'argv', ['run', 'main.py', '--argument=--port=8080'])
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
    monkeypatch.setattr(sys, 'argv', ['run', '--link-auto'])
    parsed = args("1.0.0")
    assert parsed.link_auto == -1
    
    monkeypatch.setattr(sys, 'argv', ['run', '--link-auto', '2'])
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
    monkeypatch.setattr(sys, 'argv', ['run', 'main.py', '--argument', 'initial', '--', 'extra1', 'extra2'])
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
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--directory', 'src/'])
    parsed = args("1.0.0")
    assert parsed.directory == 'src/'

    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--cwd', 'src/'])
    parsed = args("1.0.0")
    assert parsed.directory == 'src/'

def test_args_clean_flag(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', '--clean'])
    parsed = args("1.0.0")
    assert parsed.clean is True

def test_args_out_dir(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--out-dir', 'bin/'])
    parsed = args("1.0.0")
    assert parsed.out_dir == 'bin/'

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

def test_args_new_feature_flags(monkeypatch):
    # --build-only
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '-B'])
    parsed = args("1.0.0")
    assert parsed.build_only is True

    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--build-only'])
    parsed = args("1.0.0")
    assert parsed.build_only is True

    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--no-run'])
    parsed = args("1.0.0")
    assert parsed.build_only is True

    # --expect
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--expect', 'expected.txt'])
    parsed = args("1.0.0")
    assert parsed.expect == 'expected.txt'

    # --test-dir
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--test-dir', 'tests/'])
    parsed = args("1.0.0")
    assert parsed.test_dir == 'tests/'

    # --doctor
    monkeypatch.setattr(sys, 'argv', ['run', '--doctor'])
    parsed = args("1.0.0")
    assert parsed.doctor is True

    # --no-color
    monkeypatch.setattr(sys, 'argv', ['run', '--no-color'])
    parsed = args("1.0.0")
    assert parsed.no_color is True

    # --debug
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--debug'])
    parsed = args("1.0.0")
    assert parsed.debug is True

    # --gdb
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--gdb'])
    parsed = args("1.0.0")
    assert parsed.gdb is True

    # --lldb
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--lldb'])
    parsed = args("1.0.0")
    assert parsed.lldb is True

    # --valgrind
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--valgrind'])
    parsed = args("1.0.0")
    assert parsed.valgrind is True

    # --asan
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--asan'])
    parsed = args("1.0.0")
    assert parsed.asan is True

    # --tsan
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--tsan'])
    parsed = args("1.0.0")
    assert parsed.tsan is True

    # --sanitize
    monkeypatch.setattr(sys, 'argv', ['run', 'main.c', '--sanitize', 'memory,leak'])
    parsed = args("1.0.0")
    assert parsed.sanitize == 'memory,leak'

    # --new & --template
    monkeypatch.setattr(sys, 'argv', ['run', '--new', 'solution.cpp', '--template', 'cp'])
    parsed = args("1.0.0")
    assert parsed.new == 'solution.cpp'
    assert parsed.template == 'cp'
