import pytest
from pathlib import Path
from runner.core import CompilerRunner

def test_find_source_files_ignores_dirs(tmp_path):
    # Setup dummy environment
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "bad.c").write_text("")
    
    node_dir = tmp_path / "node_modules"
    node_dir.mkdir()
    (node_dir / "bad.cpp").write_text("")
    
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    good_c = src_dir / "good.c"
    good_c.write_text("")
    
    runner = CompilerRunner({}, extra_flags="", run_args="")
    # Manually configure needed properties since config might not load locally
    runner.c_family_ext = {'.c', '.cpp', '.cc'}
    runner.java_ext = {'.java'}
    runner.exclude_files = []
    runner.exclude_exts = []
    
    found = runner.find_source_files(tmp_path)
    
    # Should only find the good file, not the ones in ignored dirs
    assert str(good_c) in found
    assert str(git_dir / "bad.c") not in found
    assert str(node_dir / "bad.cpp") not in found

def test_find_source_files_max_depth(tmp_path):
    src_dir = tmp_path / "level1"
    src_dir.mkdir()
    f1 = src_dir / "f1.c"
    f1.write_text("")
    
    nested_dir = src_dir / "level2"
    nested_dir.mkdir()
    f2 = nested_dir / "f2.c"
    f2.write_text("")
    
    runner = CompilerRunner({}, extra_flags="", run_args="")
    runner.c_family_ext = {'.c'}
    runner.java_ext = set()
    runner.exclude_files = []
    runner.exclude_exts = []
    
    # Depth 0 (only current dir)
    found_0 = runner.find_source_files(tmp_path, max_depth=0)
    assert len(found_0) == 0
    
    # Depth 1 (tmp_path and level1)
    found_1 = runner.find_source_files(tmp_path, max_depth=1)
    assert str(f1) in found_1
    assert str(f2) not in found_1
    
    # Depth 2 (all)
    found_2 = runner.find_source_files(tmp_path, max_depth=2)
    assert str(f1) in found_2
    assert str(f2) in found_2

def test_run_command_use_shell(tmp_path):
    runner = CompilerRunner({"dry_run": True})
    # List command with shell=True
    assert runner.run_command(["echo hello && echo world"], use_shell=True) is True

def test_java_handler_classpath(tmp_path, monkeypatch):
    runner = CompilerRunner({"dry_run": True})
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    java_file = src_dir / "Main.java"
    java_file.write_text("public class Main { public static void main(String[] args) {} }")

    commands = []
    monkeypatch.setattr(runner, "run_command", lambda cmd, **kwargs: commands.append((cmd, kwargs)) or True)

    runner._handle_java_single_file(java_file)
    assert len(commands) == 2
    # Check that the run command passed -cp with src_dir
    exec_cmd, _ = commands[1]
    assert exec_cmd[0] == "java"
    assert "-cp" in exec_cmd
    assert str(src_dir) in exec_cmd

def test_printer_metrics(capsys):
    from util.output import Printer
    # Time only
    Printer.metrics(seconds=0.042)
    out1 = capsys.readouterr().out
    assert "Took 0.042s" in out1

    # Memory only (< 1 MB)
    Printer.metrics(memory_bytes=512 * 1024)
    out2 = capsys.readouterr().out
    assert "Peak Memory: 512.0 KB" in out2

    # Memory only (>= 1 MB)
    Printer.metrics(memory_bytes=4 * 1024 * 1024)
    out3 = capsys.readouterr().out
    assert "Peak Memory: 4.00 MB" in out3

    # Both
    Printer.metrics(seconds=0.123, memory_bytes=2 * 1024 * 1024)
    out4 = capsys.readouterr().out
    assert "Took 0.123s" in out4
    assert "Peak Memory: 2.00 MB" in out4

def test_run_command_memory_tracking(tmp_path):
    runner = CompilerRunner({"memory": True, "time": True})
    # Run a simple Python exit command
    assert runner.run_command(["python3", "-c", "import sys; sys.exit(0)"]) is True

def test_run_command_piped_stdin(tmp_path, capfd, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("piped_secret_42\n"))
    runner = CompilerRunner({"stdin": "-"})
    assert runner._buffered_stdin == "piped_secret_42\n"
    
    # Run python reading from stdin
    cmd = ["python3", "-c", "import sys; print(f'ECHO: {sys.stdin.read().strip()}')"]
    assert runner.run_command(cmd) is True
    out, _ = capfd.readouterr()
    assert "ECHO: piped_secret_42" in out
