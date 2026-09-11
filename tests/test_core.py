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
    assert runner.last_memory_bytes is not None
    assert runner.last_memory_bytes > 0

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

def test_large_stdout_pipe_no_deadlock(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Generates 128KB of output with memory tracking and expect active
    expect_file = tmp_path / "expected.txt"
    large_data = "A" * (128 * 1024)
    expect_file.write_text(large_data)

    runner = CompilerRunner({"memory": True, "expect": str(expect_file), "quiet": True})
    cmd = ["python3", "-c", f"import sys; sys.stdout.write('A' * {128 * 1024})"]
    assert runner.run_command(cmd) is True

def test_missing_stdin_file_raises_error():
    from util.errors import ExecutionError
    runner = CompilerRunner({"stdin": "nonexistent_stdin_file.txt", "quiet": True})
    with pytest.raises(ExecutionError, match="Failed to open stdin file"):
        runner.run_command(["python3", "-c", "pass"])

def test_runner_quoted_arguments_and_flags():
    # Multi-token quoted arguments must not have quotes stripped at boundaries
    runner = CompilerRunner(
        {},
        extra_flags='-DVAR="hello world" -Wall',
        run_args="'arg1' 'arg2'"
    )
    assert runner.extra_flags == ['-DVAR=hello world', '-Wall']
    assert runner.run_args == ['arg1', 'arg2']

    # Structured list support
    runner_list = CompilerRunner(
        {},
        extra_flags=['-O3', '-DDEBUG'],
        run_args=['arg with space', 'arg2']
    )
    assert runner_list.extra_flags == ['-O3', '-DDEBUG']
    assert runner_list.run_args == ['arg with space', 'arg2']

def test_python_interpreter_option_separator(monkeypatch):
    """Test Python interpreter uses -- option separator and ./ path normalization (SEC-R2-005)."""
    runner = CompilerRunner({}, extra_flags="", run_args=["foo", "bar"])
    commands_run = []
    monkeypatch.setattr(runner, "run_command", lambda cmd, **kwargs: (commands_run.append(cmd), True)[1])
    monkeypatch.setattr(runner, "_get_python_executable", lambda: "python3")

    hyphen_script = Path("-c.py")
    runner._handle_python_execution(hyphen_script)
    assert len(commands_run) == 1
    assert commands_run[0] == ["python3", "--", "./-c.py", "foo", "bar"]

    runner.flags["debug"] = True
    runner._handle_python_execution(hyphen_script)
    assert len(commands_run) == 2
    assert commands_run[1] == ["python3", "-m", "pdb", "--", "./-c.py", "foo", "bar"]

def test_node_interpreter_option_separator(monkeypatch):
    """Test Node.js interpreter uses -- option separator and ./ path normalization (SEC-R2-005)."""
    runner = CompilerRunner({}, extra_flags="", run_args=["arg1"])
    commands_run = []
    monkeypatch.setattr(runner, "run_command", lambda cmd, **kwargs: (commands_run.append(cmd), True)[1])
    monkeypatch.setattr(runner, "_get_interpreter_path", lambda names: "node")

    runner._handle_node_execution(Path("-e.js"))
    assert len(commands_run) == 1
    assert commands_run[0] == ["node", "--", "./-e.js", "arg1"]

    runner.flags["debug"] = True
    runner._handle_node_execution(Path("-e.js"))
    assert len(commands_run) == 2
    assert commands_run[1] == ["node", "--inspect-brk", "--", "./-e.js", "arg1"]

def test_bash_interpreter_option_separator(monkeypatch):
    """Test Bash interpreter uses -- option separator and ./ path normalization (SEC-R2-005)."""
    runner = CompilerRunner({}, extra_flags="", run_args=[])
    commands_run = []
    monkeypatch.setattr(runner, "run_command", lambda cmd, **kwargs: (commands_run.append(cmd), True)[1])
    monkeypatch.setattr(runner, "_get_interpreter_path", lambda names: "bash")

    runner._handle_bash_execution(Path("-s.sh"))
    assert len(commands_run) == 1
    assert commands_run[0] == ["bash", "--", "./-s.sh"]

def test_ruby_perl_lua_interpreter_option_separator(monkeypatch):
    """Test Ruby, Perl, and Lua interpreters use -- option separator (SEC-R2-005)."""
    runner = CompilerRunner({}, extra_flags="", run_args=[])
    commands_run = []
    monkeypatch.setattr(runner, "run_command", lambda cmd, **kwargs: (commands_run.append(cmd), True)[1])

    monkeypatch.setattr(runner, "_get_interpreter_path", lambda names: "ruby")
    runner._handle_ruby_execution(Path("-r.rb"))
    assert commands_run[-1] == ["ruby", "--", "./-r.rb"]

    monkeypatch.setattr(runner, "_get_interpreter_path", lambda names: "perl")
    runner._handle_perl_execution(Path("-e.pl"))
    assert commands_run[-1] == ["perl", "--", "./-e.pl"]

    monkeypatch.setattr(runner, "_get_interpreter_path", lambda names: "lua")
    runner._handle_lua_execution(Path("-l.lua"))
    assert commands_run[-1] == ["lua", "--", "./-l.lua"]

def test_java_path_normalization_and_main_class_validation(monkeypatch):
    """Test Java compiler path normalization and main class rejection of hyphens (SEC-R2-005)."""
    from util.errors import ExecutionError
    runner = CompilerRunner({}, extra_flags="", run_args=[])
    commands_run = []
    monkeypatch.setattr(runner, "run_command", lambda cmd, **kwargs: (commands_run.append(cmd), True)[1])
    monkeypatch.setattr("runner.java_handler.JPM.record_class_files", lambda dirs: set())
    monkeypatch.setattr("runner.java_handler.JPM.get_new_class_files", lambda dirs, before: [])
    monkeypatch.setattr("runner.java_handler.JPM.get_main_class", lambda fp: "ValidMain")

    runner._handle_java_single_file(Path("-Main.java"))
    assert any("./-Main.java" in cmd for cmd in commands_run)

    monkeypatch.setattr("runner.java_handler.JPM.get_main_class", lambda fp: "-InvalidMain")
    with pytest.raises(ExecutionError, match="Invalid main class name"):
        runner._handle_java_single_file(Path("Main.java"))

def test_c_and_rust_source_path_normalization(monkeypatch):
    """Test C/C++ and Rust compiler invocations prefix hyphens with ./ (SEC-R2-005)."""
    runner = CompilerRunner({}, extra_flags="", run_args=[])
    commands_run = []
    monkeypatch.setattr(runner, "run_command", lambda cmd, **kwargs: (commands_run.append(cmd), True)[1])
    monkeypatch.setattr(runner, "_execute_binary", lambda out: True)

    runner._handle_c_family_single_file(Path("-main.c"))
    compile_cmd = next(c for c in commands_run if "-o" in c)
    assert "./-main.c" in compile_cmd

    commands_run.clear()
    monkeypatch.setattr(runner, "_find_cargo_toml", lambda fp: None)
    runner._handle_rust_execution(Path("-main.rs"))
    rustc_cmd = next(c for c in commands_run if "-o" in c)
    assert "./-main.rs" in rustc_cmd

