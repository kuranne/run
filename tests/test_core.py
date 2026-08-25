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
