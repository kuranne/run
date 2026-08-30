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
        assert "--die-with-parent" in wrapped
        assert "--new-session" in wrapped
        assert "--unshare-net" in wrapped
        assert "--bind" in wrapped
        assert "/workspace" in wrapped
        assert wrapped[-4:] == cmd

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

    def test_macos_restriction_safety(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        cmd = ["ls", "-la"]
        wrapped = NativeRestrictor.wrap_command(cmd)
        assert wrapped == cmd

        # Execute macos_preexec_fn safely without exceptions
        NativeRestrictor.macos_preexec_fn()

    def test_unsupported_os_raises_config_error(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        with pytest.raises(ConfigError, match="not supported on this OS"):
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
        assert wrapped[:7] == ["docker", "compose", "-f", "docker-compose.yml", "exec", "-w", os.getcwd()]
        assert wrapped[7] == "app"
        assert wrapped[8:] == cmd


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
        assert "--security-opt=no-new-privileges" in calls[0]
        assert "--cap-drop=ALL" in calls[0]
        assert "/tmp:/tmp" not in calls[0]

        wrapped = PersistentSandbox.wrap_command(["echo", "1"])
        assert wrapped == ["docker", "exec", "-w", os.getcwd(), "container-id-abc", "echo", "1"]

        PersistentSandbox.stop()
        assert PersistentSandbox._container_id is None
        assert calls[-1] == ["docker", "stop", "container-id-abc"]

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
