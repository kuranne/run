import os
import sys
import time
import pytest
import signal
from pathlib import Path
from runner.core import CompilerRunner
from runner.base_runner import BaseRunner
from runner.project_runner import ProjectRunner
from util.config import Config
from util.substitutions import VariableSubstitutor
from util.errors import ExecutionError, CompilationError, ConfigError
from util.sandbox import NativeRestrictor, ContainerSandbox, PersistentSandbox, ComposeSandbox

def test_large_stdout_expect_matching(tmp_path):
    """Test BaseRunner with large stdout (>256KB) matching --expect file to ensure no pipe deadlock."""
    payload_size = 256 * 1024
    payload = "X" * payload_size + "\n"
    
    expect_file = tmp_path / "expected_large.txt"
    expect_file.write_text(payload)

    runner = CompilerRunner({"expect": str(expect_file), "quiet": True})
    # Run python writing large payload to stdout
    cmd = [sys.executable, "-c", f"import sys; sys.stdout.write('X' * {payload_size} + '\\n')"]
    assert runner.run_command(cmd) is True

def test_large_stdout_expect_mismatch(tmp_path):
    """Test BaseRunner with large stdout (>256KB) mismatching --expect file."""
    payload_size = 256 * 1024
    expect_file = tmp_path / "expected_diff.txt"
    expect_file.write_text("Y" * payload_size + "\n")

    runner = CompilerRunner({"expect": str(expect_file), "quiet": True})
    cmd = [sys.executable, "-c", f"import sys; sys.stdout.write('X' * {payload_size} + '\\n')"]
    # Mismatch should return False cleanly without hang or crash
    assert runner.run_command(cmd) is False

def test_large_stdout_memory_tracking_posix():
    """Test BaseRunner with large stdout (>500KB) under memory tracking (-M)."""
    payload_size = 500 * 1024
    runner = CompilerRunner({"memory": True, "quiet": True})
    cmd = [sys.executable, "-c", f"import sys; sys.stdout.write('A' * {payload_size})"]
    assert runner.run_command(cmd) is True

def test_large_stdout_and_expect_and_memory(tmp_path):
    """Test BaseRunner combining both -M and --expect with 512KB payload."""
    payload_size = 512 * 1024
    payload = "B" * payload_size
    
    expect_file = tmp_path / "expected_b.txt"
    expect_file.write_text(payload)

    runner = CompilerRunner({"memory": True, "expect": str(expect_file), "quiet": True})
    cmd = [sys.executable, "-c", f"import sys; sys.stdout.write('B' * {payload_size})"]
    assert runner.run_command(cmd) is True

def test_missing_stdin_file_raises_clean_execution_error():
    """Verify missing stdin path raises clean ExecutionError immediately."""
    runner = CompilerRunner({"stdin": "non_existent_path_xyz_12345.txt", "quiet": True})
    with pytest.raises(ExecutionError, match="Failed to open stdin file"):
        runner.run_command([sys.executable, "-c", "pass"])

def test_directory_as_stdin_file_raises_execution_error(tmp_path):
    """Verify passing a directory path as stdin file raises ExecutionError."""
    runner = CompilerRunner({"stdin": str(tmp_path), "quiet": True})
    with pytest.raises(ExecutionError, match="Failed to open stdin file"):
        runner.run_command([sys.executable, "-c", "pass"])

def test_buffered_stdin_piped_mode(monkeypatch, capfd):
    """Test stdin='-' reading buffered sys.stdin data."""
    import io
    test_input = "SecretKey_987654321\nSecondLine\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(test_input))
    
    runner = CompilerRunner({"stdin": "-"})
    assert runner._buffered_stdin == test_input

    cmd = [sys.executable, "-c", "import sys; data = sys.stdin.read(); print(f'READ:{data.strip()}')"]
    assert runner.run_command(cmd) is True
    out, _ = capfd.readouterr()
    assert "READ:SecretKey_987654321\nSecondLine" in out

def test_timeout_handling_kills_process():
    """Test timeout properly aborts and terminates long-running process."""
    runner = CompilerRunner({"timeout": 1, "quiet": True})
    # Run process sleeping for 10 seconds with 1 second timeout
    cmd = [sys.executable, "-c", "import time; time.sleep(10)"]
    start = time.perf_counter()
    with pytest.raises(ExecutionError, match="Execution timed out after 1 seconds"):
        runner.run_command(cmd)
    duration = time.perf_counter() - start
    # Should terminate around 1s, definitely well below 4s
    assert duration < 4.0

def test_compound_command_double_ampersand_success(tmp_path, monkeypatch):
    """Test compound command with '&&' runs all steps sequentially when succeeding."""
    toml = tmp_path / "Run.toml"
    toml.write_text("""
    [projects.compound]
    file = "app.manifest"
    build = "echo step1 && echo step2 && echo step3"
    run = "echo run1 && echo run2"
    """)
    manifest = tmp_path / "app.manifest"
    manifest.write_text("")
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = Config()

    class TraceRunner:
        def __init__(self):
            self.flags = {}
            self.log = []
        def run_command(self, cmd, use_shell=False, compiling=False):
            self.log.append((cmd, compiling))
            return True

    runner = TraceRunner()
    detected = ProjectRunner.detect_project(tmp_path, config)
    assert detected is not None

    success = ProjectRunner.run_project(detected, runner, extra_flags=["--flag"], run_args=["--arg"])
    assert success is True
    assert len(runner.log) == 5
    assert runner.log[0] == (["echo", "step1"], True)
    assert runner.log[1] == (["echo", "step2"], True)
    assert runner.log[2] == (["echo", "step3", "--flag"], True)
    assert runner.log[3] == (["echo", "run1"], False)
    assert runner.log[4] == (["echo", "run2", "--arg"], False)

def test_compound_command_double_ampersand_failure_stops_execution(tmp_path, monkeypatch):
    """Test compound command with '&&' stops immediately when a middle step fails."""
    toml = tmp_path / "Run.toml"
    toml.write_text("""
    [projects.failing]
    file = "app.manifest"
    build = "echo step1 && echo fail_step && echo step3"
    run = "echo run1"
    """)
    manifest = tmp_path / "app.manifest"
    manifest.write_text("")
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = Config()

    class FailingRunner:
        def __init__(self):
            self.flags = {}
            self.log = []
        def run_command(self, cmd, use_shell=False, compiling=False):
            self.log.append(cmd)
            if "fail_step" in cmd:
                return False
            return True

    runner = FailingRunner()
    detected = ProjectRunner.detect_project(tmp_path, config)
    assert detected is not None

    success = ProjectRunner.run_project(detected, runner)
    assert success is False
    # Should have run step1 and fail_step, but NEVER step3 or run1
    assert len(runner.log) == 2
    assert runner.log[0] == ["echo", "step1"]
    assert runner.log[1] == ["echo", "fail_step"]

def test_compound_command_semicolon(tmp_path, monkeypatch):
    """Test compound command with ';' runs steps sequentially."""
    toml = tmp_path / "Run.toml"
    toml.write_text("""
    [projects.semi]
    file = "app.manifest"
    command = "echo first ; echo second"
    """)
    manifest = tmp_path / "app.manifest"
    manifest.write_text("")
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = Config()

    class TraceRunner:
        def __init__(self):
            self.flags = {}
            self.log = []
        def run_command(self, cmd, use_shell=False, compiling=False):
            self.log.append((cmd, compiling))
            return True

    runner = TraceRunner()
    detected = ProjectRunner.detect_project(tmp_path, config)
    assert detected is not None

    success = ProjectRunner.run_project(detected, runner, extra_flags=["--extra"])
    assert success is True
    assert len(runner.log) == 2
    assert runner.log[0] == (["echo", "first"], False)
    assert runner.log[1] == (["echo", "second", "--extra"], False)

def test_variable_substitutor_env_var_no_literal_quotes(monkeypatch):
    """Ensure ${env:VAR} does NOT inject literal shell quotes into arg lists."""
    monkeypatch.setenv("MY_COMPILER_FLAGS", "-O3 -march=native -Wall")
    monkeypatch.setenv("MY_PATH", "/path with spaces/to/bin")

    ctx = {}
    res_str = VariableSubstitutor.substitute_string("gcc ${env:MY_COMPILER_FLAGS} -o out", ctx)
    assert res_str == "gcc -O3 -march=native -Wall -o out"
    assert "'" not in res_str
    assert '"' not in res_str

    res_list = VariableSubstitutor.substitute_list(["--target=${env:MY_PATH}", "${env:MY_COMPILER_FLAGS}"], ctx)
    assert res_list == ["--target=/path with spaces/to/bin", "-O3 -march=native -Wall"]
    # Verify no spurious quotes
    assert res_list[0] == "--target=/path with spaces/to/bin"

def test_variable_substitutor_unset_env_var():
    """Ensure unset ${env:UNSET_VAR} returns empty string."""
    ctx = {}
    res = VariableSubstitutor.substitute_string("prefix_${env:DEFINITELY_NOT_SET_12345}_suffix", ctx)
    assert res == "prefix__suffix"

def test_sandbox_config_validation_malformed(tmp_path, monkeypatch):
    """Test Config.validate() against various malformed sandbox configurations."""
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

    # 1. Invalid image starting with hyphen
    toml = tmp_path / "Run.toml"
    toml.write_text('[sandbox]\nimage = "--entrypoint=sh"\n')
    with pytest.raises(ValueError, match="Invalid sandbox image name"):
        Config()

    # 2. Invalid image containing spaces
    toml.write_text('[sandbox]\nimage = "ubuntu:latest; rm -rf /"\n')
    with pytest.raises(ValueError, match="Invalid sandbox image name"):
        Config()

    # 3. Empty image
    toml.write_text('[sandbox]\nimage = "   "\n')
    with pytest.raises(ValueError, match="sandbox 'image' must be a non-empty string"):
        Config()

    # 4. Non-existent Dockerfile
    toml.write_text('[sandbox]\ndockerfile = "does_not_exist.Dockerfile"\n')
    with pytest.raises(ValueError, match="Configured sandbox Dockerfile '.*' not found"):
        Config()

    # 5. Non-existent compose file
    toml.write_text('[sandbox]\ncompose = "missing-compose.yml"\n')
    with pytest.raises(ValueError, match="Configured sandbox compose file '.*' not found"):
        Config()

    # 6. Empty compose_service
    toml.write_text('[sandbox]\ncompose_service = ""\n')
    with pytest.raises(ValueError, match="sandbox 'compose_service' must be a non-empty string"):
        Config()

    # 7. Non-boolean sandbox_net
    toml.write_text('[sandbox]\nsandbox_net = "true"\n')
    with pytest.raises(ValueError, match="sandbox 'sandbox_net' must be a boolean"):
        Config()

    # 8. Non-boolean restrict
    toml.write_text('[sandbox]\nrestrict = "yes"\n')
    with pytest.raises(ValueError, match="sandbox 'restrict' must be a boolean"):
        Config()

    # 9. Non-dict sandbox section
    toml.write_text('sandbox = "enabled"\n')
    with pytest.raises(ValueError, match="'sandbox' section must be a table"):
        Config()

def test_persistent_sandbox_lifecycle(monkeypatch):
    """Test PersistentSandbox lifecycle tracking, wrap_command and cleanup."""
    calls = []
    def mock_run(cmd, capture_output=False, text=False):
        calls.append(cmd)
        class MockRes:
            returncode = 0
            stdout = "mock_container_id_789\n"
            stderr = ""
        return MockRes()

    import subprocess as spc
    monkeypatch.setattr(spc, "run", mock_run)

    # Wrap before start should raise ExecutionError
    PersistentSandbox._container_id = None
    with pytest.raises(ExecutionError, match="Persistent container is not running"):
        PersistentSandbox.wrap_command(["ls", "-l"])

    # Start
    PersistentSandbox.start(engine="docker", image="alpine:latest", net=False, cwd="/test/dir")
    assert PersistentSandbox._container_id == "mock_container_id_789"
    
    # Wrap after start
    wrapped = PersistentSandbox.wrap_command(["gcc", "main.c"])
    assert wrapped[:5] == ["docker", "exec", "-w", os.getcwd(), "mock_container_id_789"]
    assert wrapped[5:] == ["gcc", "main.c"]

    # Stop
    PersistentSandbox.stop()
    assert PersistentSandbox._container_id is None
