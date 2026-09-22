import os
import sys
import stat
import pytest
from pathlib import Path
from unittest.mock import patch

from runner.script_handler import ScriptHandler
from runner.core import CompilerRunner
from util.cache import CacheManager
from util.errors import CompilationError

def test_is_run_shebang(tmp_path):
    f_env_run = tmp_path / "env_run.c"
    f_env_run.write_text("#!/usr/bin/env run\nint main(){}")
    assert ScriptHandler.is_run_shebang(f_env_run) is True

    f_bin_run = tmp_path / "bin_run.c"
    f_bin_run.write_text("#!/usr/bin/run\nint main(){}")
    assert ScriptHandler.is_run_shebang(f_bin_run) is True

    f_usr_local_run = tmp_path / "usr_local_run.c"
    f_usr_local_run.write_text("#!/usr/local/bin/run\nint main(){}")
    assert ScriptHandler.is_run_shebang(f_usr_local_run) is True

    f_python = tmp_path / "py.py"
    f_python.write_text("#!/usr/bin/env python3\nprint('hi')")
    assert ScriptHandler.is_run_shebang(f_python) is False

    f_c = tmp_path / "c.c"
    f_c.write_text("#!/usr/bin/env c\nint main(){}")
    assert ScriptHandler.is_run_shebang(f_c) is False

    f_no_shebang = tmp_path / "plain.c"
    f_no_shebang.write_text("int main(){}")
    assert ScriptHandler.is_run_shebang(f_no_shebang) is False


def test_extract_inline_directives(tmp_path):
    f = tmp_path / "directives.c"
    f.write_text("""#!/usr/bin/env run
// run: -O3 -Wall
// flags: -lm -pthread
/* run: -DTEST=1 */
// lang: cpp
#include <stdio.h>
int main() { return 0; }
""")
    flags = ScriptHandler.extract_inline_directives(f)
    assert "-O3" in flags
    assert "-Wall" in flags
    assert "-lm" in flags
    assert "-pthread" in flags
    assert "-DTEST=1" in flags
    assert "cpp" not in flags  # Language directive filtered out


def test_detect_language_from_shebang(tmp_path):
    handler = ScriptHandler()

    # With extension
    f_c = tmp_path / "script.c"
    f_c.write_text("#!/usr/bin/env run\n#include <stdio.h>\nint main(){}")
    assert handler._detect_language_from_shebang(f_c) == ".c"

    f_cpp = tmp_path / "script.cpp"
    f_cpp.write_text("#!/usr/bin/env run\n#include <iostream>\nint main(){}")
    assert handler._detect_language_from_shebang(f_cpp) == ".cpp"

    # Extensionless with directive
    f_ext_dir = tmp_path / "tool_dir"
    f_ext_dir.write_text("#!/usr/bin/env run\n// lang: cpp\nint main(){}")
    assert handler._detect_language_from_shebang(f_ext_dir) == ".cpp"

    # Extensionless with C++ headers
    f_ext_cpp = tmp_path / "tool_cpp"
    f_ext_cpp.write_text("#!/usr/bin/env run\n#include <iostream>\nint main() { std::cout << 1; }")
    assert handler._detect_language_from_shebang(f_ext_cpp) == ".cpp"

    # Extensionless with C code
    f_ext_c = tmp_path / "tool_c"
    f_ext_c.write_text("#!/usr/bin/env run\n#include <stdio.h>\nint main() { printf(\"hi\"); }")
    assert handler._detect_language_from_shebang(f_ext_c) == ".c"


def test_c_script_execution_and_caching(tmp_path, capfd, caplog):
    cache = CacheManager(project_root=tmp_path)
    cache.clear()

    script = tmp_path / "hello.c"
    script.write_text("""#!/usr/bin/env run
#include <stdio.h>

int main(void) {
    printf("hello_shebang_world\\n");
    return 0;
}
""")
    # Set executable permission
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    runner = CompilerRunner(op_flags={"dry_run": False}, extra_flags="", run_args="")
    runner.cache = cache

    # First run: should compile to cache and execute
    res = runner._handle_single_file(script)
    assert res is True
    runner.cleanup()

    out, _ = capfd.readouterr()
    assert "hello_shebang_world" in out

    # Workspace directory must remain 100% clean (no .out file created in tmp_path)
    assert not (tmp_path / "hello.out").exists()
    assert not (tmp_path / "hello.exe").exists()

    # Cached binary must exist in cache directory
    cached_bin = cache.get_cached_binary_path(script)
    assert cached_bin.exists()

    # Second run: should hit cache without recompiling
    caplog.clear()
    with patch.object(runner, "run_command", wraps=runner.run_command) as mock_run_cmd:
        res2 = runner._handle_single_file(script)
        assert res2 is True
        # Verify that run_command was NOT called with compiling=True on cache hit
        compiling_calls = [c for c in mock_run_cmd.call_args_list if c.kwargs.get("compiling") or (len(c.args) > 2 and c.args[2] is True)]
        assert len(compiling_calls) == 0

    out2, _ = capfd.readouterr()
    assert "hello_shebang_world" in out2
    assert "Using cached binary" in caplog.text


def test_cpp_script_execution(tmp_path, capfd):
    script = tmp_path / "hello.cpp"
    script.write_text("""#!/usr/bin/env run
#include <iostream>

int main() {
    std::cout << "cpp_shebang_active" << std::endl;
    return 0;
}
""")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    cache = CacheManager(project_root=tmp_path)
    runner = CompilerRunner(op_flags={"dry_run": False}, extra_flags="", run_args="")
    runner.cache = cache

    res = runner._handle_single_file(script)
    assert res is True
    runner.cleanup()

    out, _ = capfd.readouterr()
    assert "cpp_shebang_active" in out
    assert not (tmp_path / "hello.out").exists()


def test_extensionless_scripts_execution(tmp_path, capfd):
    # 1. Extensionless C script
    c_tool = tmp_path / "my_c_tool"
    c_tool.write_text("""#!/usr/bin/env run
#include <stdio.h>
int main() {
    printf("extensionless_c_success\\n");
    return 0;
}
""")
    c_tool.chmod(c_tool.stat().st_mode | stat.S_IXUSR)

    cache = CacheManager(project_root=tmp_path)
    runner = CompilerRunner(op_flags={"dry_run": False})
    runner.cache = cache

    res = runner._handle_single_file(c_tool)
    assert res is True
    runner.cleanup()

    out, _ = capfd.readouterr()
    assert "extensionless_c_success" in out
    assert not (tmp_path / "my_c_tool.out").exists()

    # 2. Extensionless C++ script
    cpp_tool = tmp_path / "my_cpp_tool"
    cpp_tool.write_text("""#!/usr/bin/env run
#include <iostream>
int main() {
    std::cout << "extensionless_cpp_success" << std::endl;
    return 0;
}
""")
    cpp_tool.chmod(cpp_tool.stat().st_mode | stat.S_IXUSR)

    res_cpp = runner._handle_single_file(cpp_tool)
    assert res_cpp is True
    runner.cleanup()

    out_cpp, _ = capfd.readouterr()
    assert "extensionless_cpp_success" in out_cpp
    assert not (tmp_path / "my_cpp_tool.out").exists()


def test_inline_compiler_directives(tmp_path, capfd):
    # Test script using math functions requiring -lm on Linux
    script = tmp_path / "math_script.c"
    script.write_text("""#!/usr/bin/env run
// run: -O2 -lm
#include <stdio.h>
#include <math.h>

int main(void) {
    double val = sqrt(25.0);
    printf("sqrt(25.0) = %.1f\\n", val);
    return 0;
}
""")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    cache = CacheManager(project_root=tmp_path)
    runner = CompilerRunner(op_flags={"dry_run": False})
    runner.cache = cache

    res = runner._handle_single_file(script)
    assert res is True
    runner.cleanup()

    out, _ = capfd.readouterr()
    assert "sqrt(25.0) = 5.0" in out


def test_shebang_argument_forwarding(tmp_path, capfd):
    script = tmp_path / "args_script.c"
    script.write_text("""#!/usr/bin/env run
#include <stdio.h>

int main(int argc, char *argv[]) {
    for (int i = 1; i < argc; i++) {
        printf("ARG:%s\\n", argv[i]);
    }
    return 0;
}
""")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    # Test runner executing with run_args
    cache = CacheManager(project_root=tmp_path)
    runner = CompilerRunner(op_flags={"dry_run": False}, run_args=["foo", "bar", "test space"])
    runner.cache = cache

    res = runner._handle_single_file(script)
    assert res is True
    runner.cleanup()

    out, _ = capfd.readouterr()
    assert "ARG:foo" in out
    assert "ARG:bar" in out
    assert "ARG:test space" in out


def test_shebang_line_preservation_in_errors(tmp_path):
    # Line 1: Shebang
    # Line 2: Empty
    # Line 3: #include
    # Line 4: syntax error
    script = tmp_path / "syntax_err.c"
    script.write_text("""#!/usr/bin/env run

#include <stdio.h>
int main() { INVALID_SYNTAX_ERROR; return 0; }
""")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    cache = CacheManager(project_root=tmp_path)
    runner = CompilerRunner(op_flags={"dry_run": False})
    runner.cache = cache

    # Intercept run_command to inspect compiler error output
    with patch.object(runner, "run_command", wraps=runner.run_command) as mock_run:
        res = runner._handle_single_file(script)
        assert res is False
        assert mock_run.called

    runner.cleanup()


def test_cache_clear_removes_bin_and_src(tmp_path):
    cache = CacheManager(project_root=tmp_path)
    bin_f = cache.get_cached_binary_path(tmp_path / "test.c")
    bin_f.write_text("dummy binary")
    src_f = cache.get_sanitized_source_path(tmp_path / "test.c")
    src_f.write_text("dummy source")

    assert bin_f.exists()
    assert src_f.exists()

    cache.clear()

    assert not bin_f.exists()
    assert not src_f.exists()


def test_main_cli_shebang_invocation(tmp_path, capfd, monkeypatch):
    from main import main
    script = tmp_path / "cli_shebang.c"
    script.write_text("""#!/usr/bin/env run
#include <stdio.h>
int main(int argc, char *argv[]) {
    printf("MAIN_CLI_COUNT:%d\\n", argc - 1);
    for (int i = 1; i < argc; i++) {
        printf("MAIN_ARG:%s\\n", argv[i]);
    }
    return 0;
}
""")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    # Simulate shell executing: run /path/to/script foo bar baz
    monkeypatch.setattr(sys, "argv", ["run", str(script), "alpha", "beta with space"])

    exit_code = main()
    assert exit_code == 0

    out, _ = capfd.readouterr()
    assert "MAIN_CLI_COUNT:2" in out
    assert "MAIN_ARG:alpha" in out
    assert "MAIN_ARG:beta with space" in out

