import os
import tempfile
from pathlib import Path
import pytest
from util.glob_matcher import (
    compile_glob,
    has_glob_wildcard,
    match_path,
    match_extension,
    find_matching_manifests,
)
from util.config import Config
from runner.project_runner import ProjectRunner
from runner.core import CompilerRunner

def test_has_glob_wildcard():
    """Verify glob wildcard detection."""
    assert has_glob_wildcard("*.sln")
    assert has_glob_wildcard("test_?.c")
    assert has_glob_wildcard("[a-z].py")
    assert not has_glob_wildcard("Makefile")
    assert not has_glob_wildcard("CMakeLists.txt")


def test_match_path_filename_glob():
    """Verify filename-only glob matching without slashes."""
    assert match_path(Path("foo/bar/temp.tmp"), "*.tmp")
    assert match_path(Path("foo/bar/test_case.c"), "test_*")
    assert match_path(Path("secret.py"), "secret.py")
    assert not match_path(Path("foo/bar/temp.c"), "*.tmp")
    assert not match_path(Path("foo/bar/prod_case.c"), "test_*")


def test_match_path_path_glob_with_slash():
    """Verify relative path matching with slashes and recursive wildcards."""
    root = Path("/workspace/project")
    
    # Single directory wildcard
    assert match_path(root / "tests" / "test1.c", "tests/*", root_dir=root)
    assert not match_path(root / "tests" / "sub" / "test1.c", "tests/*", root_dir=root)

    # Recursive directory wildcard **
    assert match_path(root / "src" / "vendor" / "lib.c", "src/vendor/**", root_dir=root)
    assert match_path(root / "src" / "vendor" / "sub" / "lib.c", "src/vendor/**", root_dir=root)
    assert match_path(root / "tests" / "a" / "b" / "mock_test.c", "tests/**/mock_*.c", root_dir=root)
    assert not match_path(root / "src" / "a" / "b" / "mock_test.c", "tests/**/mock_*.c", root_dir=root)


def test_match_path_case_sensitivity():
    """Verify case sensitivity behavior."""
    assert match_path(Path("test.TMP"), "*.TMP", case_sensitive=True)
    assert not match_path(Path("test.TMP"), "*.tmp", case_sensitive=True)
    assert match_path(Path("test.TMP"), "*.tmp", case_sensitive=False)


def test_match_extension_normalization():
    """Verify flexible extension matching and wildcards."""
    assert match_extension(".c", ".c")
    assert match_extension(".c", "c")
    assert match_extension(".c", "*.c")
    assert match_extension(".cpp", "*.c*")
    assert match_extension(".tmp1", ".tmp*")
    assert match_extension(".tmp2", "tmp*")
    assert match_extension(".tmp3", "*.tmp*")
    assert not match_extension(".c", ".cpp")
    assert not match_extension(".c", "cpp")
    assert not match_extension(".C", ".c", case_sensitive=True)
    assert match_extension(".C", ".c", case_sensitive=False)


def test_find_matching_manifests():
    """Verify manifest file discovery and alphabetical sorting."""
    with tempfile.TemporaryDirectory() as td:
        dir_path = Path(td)
        (dir_path / "SolutionB.sln").touch()
        (dir_path / "SolutionA.sln").touch()
        (dir_path / "Other.txt").touch()

        matches = find_matching_manifests(dir_path, "*.sln")
        assert len(matches) == 2
        assert matches[0].name == "SolutionA.sln"
        assert matches[1].name == "SolutionB.sln"


def test_config_exclude_dirs_validation():
    """Verify Run.toml exclude_dirs validation and parsing."""
    with tempfile.TemporaryDirectory() as td:
        conf_path = Path(td) / "Run.toml"
        conf_path.write_text("""
[core]
exclude_files = ["*.tmp", "mock_*"]
exclude_dirs = ["tmp_*", "vendor"]
exclude_extensions = ["*.bak", ".swp"]
""")
        cfg = Config(start_dir=Path(td))
        excludes = cfg.get_exclude()
        assert excludes["files"] == ["*.tmp", "mock_*"]
        assert excludes["dirs"] == ["tmp_*", "vendor"]
        assert excludes["extensions"] == ["*.bak", ".swp"]


def test_project_manifest_glob_detection():
    """Verify project detection with glob pattern manifest."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "Run.toml").write_text("""
[projects.dotnet]
file = "*.sln"
command = "dotnet build ${file}"
""")
        (root / "App.sln").touch()
        cfg = Config(start_dir=root)
        detected = ProjectRunner.detect_project(root, cfg)
        assert detected is not None
        proj_name, proj_cfg, manifest_path = detected
        assert proj_name == "dotnet"
        assert manifest_path.name == "App.sln"


def test_project_manifest_glob_multiple_matches_warning(caplog):
    """Verify warning logging and deterministic selection when multiple manifest files match."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "Run.toml").write_text("""
[projects.dotnet]
file = "*.sln"
command = "dotnet build ${file}"
""")
        (root / "Beta.sln").touch()
        (root / "Alpha.sln").touch()
        cfg = Config(start_dir=root)
        detected = ProjectRunner.detect_project(root, cfg)
        assert detected is not None
        proj_name, proj_cfg, manifest_path = detected
        assert manifest_path.name == "Alpha.sln"

        assert "Multiple manifest files matched '*.sln'" in caplog.text
        assert "Alpha.sln" in caplog.text
        assert "Beta.sln" in caplog.text


def test_core_runner_glob_directory_and_file_exclusions(monkeypatch):
    """Verify core recursive file discovery respects glob exclusions for dirs, files, and extensions."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "Run.toml").write_text("""
[core]
exclude_files = ["*.tmp.c", "tests/**/mock_*.c"]
exclude_dirs = ["vendor*", "tmp_*"]
exclude_extensions = ["*.bak", ".skip"]
""")
        monkeypatch.chdir(root)
        # Valid files
        valid1 = root / "main.c"
        valid1.touch()
        sub_dir = root / "src"
        sub_dir.mkdir()
        valid2 = sub_dir / "helper.c"
        valid2.touch()

        # Excluded by file glob
        (root / "scratch.tmp.c").touch()
        tests_dir = root / "tests" / "unit"
        tests_dir.mkdir(parents=True)
        (tests_dir / "mock_service.c").touch()

        # Excluded by dir glob
        vendor_dir = root / "vendor_libs"
        vendor_dir.mkdir()
        (vendor_dir / "lib.c").touch()

        tmp_dir = root / "tmp_build"
        tmp_dir.mkdir()
        (tmp_dir / "output.c").touch()

        # Excluded by extension glob
        (root / "backup.bak").touch()
        (root / "ignored.skip").touch()

        runner = CompilerRunner({"quiet": False})
        found = runner.find_source_files(root)
        found_names = {Path(f).name for f in found}

        assert "main.c" in found_names
        assert "helper.c" in found_names
        assert "scratch.tmp.c" not in found_names
        assert "mock_service.c" not in found_names
        assert "lib.c" not in found_names
        assert "output.c" not in found_names
        assert "backup.bak" not in found_names
        assert "ignored.skip" not in found_names


def test_core_runner_single_file_glob_skip(monkeypatch, caplog):
    """Verify _handle_single_file skips files matching glob patterns."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "Run.toml").write_text("""
[core]
exclude_files = ["test_*.c", "*.tmp.cpp"]
exclude_extensions = [".bak"]
""")
        monkeypatch.chdir(root)
        runner = CompilerRunner({"quiet": False})

        # Single file excluded by file pattern
        file_to_skip = root / "test_something.c"
        file_to_skip.touch()
        res = runner._handle_single_file(file_to_skip)
        assert res is True  # Skipped successfully
        assert "is in exclude files" in caplog.text

        # Single file excluded by extension pattern
        ext_skip = root / "foo.bak"
        ext_skip.touch()
        res2 = runner._handle_single_file(ext_skip)
        assert res2 is None
        assert "exclude extensions" in caplog.text


def test_compile_glob_redos_prevention():
    """Verify that nested/repeated globstars do not cause exponential backtracking (ReDoS)."""
    import time
    from util.glob_matcher import compile_glob, match_path

    # Adversarial nested glob pattern with repeating wildcards
    pattern = "**/**/**/**/**/**/**/target.txt"
    # Adversarial deep path that does NOT match at the end
    non_matching_path = "/".join(["segment"] * 25) + "/mismatch.txt"

    start_time = time.perf_counter()
    regex = compile_glob(pattern)
    matched = regex.match(non_matching_path)
    elapsed = time.perf_counter() - start_time

    assert matched is None
    # Must complete in well under 0.1s (typically < 1ms)
    assert elapsed < 0.1

    # Also check match_path with repeated globstars
    assert not match_path(non_matching_path, pattern)
    assert match_path("a/b/c/target.txt", pattern)


def test_match_path_external_symlink_exclusion(tmp_path):
    """Verify that symlinks pointing outside workspace are matched by logical path rather than resolved target."""
    import os
    from util.glob_matcher import match_path

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    vendor_dir = workspace / "vendor"
    vendor_dir.mkdir()

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "external_lib.c"
    outside_file.write_text("void external() {}")

    # Symlink vendor/lib.c -> outside/external_lib.c
    symlink_path = vendor_dir / "lib.c"
    try:
        symlink_path.symlink_to(outside_file)
    except OSError:
        pytest.skip("Symlinks not supported in this environment")

    # Pattern should match vendor/lib.c even though resolved target is outside workspace
    assert match_path(symlink_path, "vendor/**", root_dir=workspace)
    assert match_path(symlink_path, "vendor/*.c", root_dir=workspace)
    assert match_path(symlink_path, "vendor/lib.c", root_dir=workspace)
    assert not match_path(symlink_path, "src/**", root_dir=workspace)


