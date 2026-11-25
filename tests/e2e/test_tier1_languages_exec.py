"""Tier 1 E2E tests: F01 (Single-file languages), F02 (C/C++ shebang & directives), F03 (Multi-file -m & -L)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import subprocess

import pytest

from tests.e2e.conftest import require_optional_tool, strip_ansi

pytestmark = pytest.mark.tier1


def _combined(res: subprocess.CompletedProcess[str]) -> str:
    return f"{res.stdout}\n{res.stderr}"


# ---------------------------------------------------------------------------
# F01: Single-File Built-in Languages
# ---------------------------------------------------------------------------


def test_f01_c_single_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    src = work_dir / "hello.c"
    src.write_text(
        '#include <stdio.h>\nint main(void) {\n    printf("hello_c_e2e\\n");\n    return 0;\n}\n',
        encoding="utf-8",
    )
    res = run_cli(["hello.c"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "hello_c_e2e" in out
    assert "[ RUN ]" in out


def test_f01_cpp_single_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    src = work_dir / "hello.cpp"
    src.write_text(
        '#include <iostream>\nint main() {\n    std::cout << "hello_cpp_e2e\\n";\n    return 0;\n}\n',
        encoding="utf-8",
    )
    res = run_cli(["hello.cpp"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "hello_cpp_e2e" in out
    assert "[ COMPILE ]" in out


def test_f01_cc_single_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    src = work_dir / "hello.cc"
    src.write_text(
        '#include <iostream>\nint main() {\n    std::cout << "hello_cc_e2e\\n";\n    return 0;\n}\n',
        encoding="utf-8",
    )
    res = run_cli(["hello.cc"])
    assert res.returncode == 0, _combined(res)
    assert "hello_cc_e2e" in strip_ansi(res.stdout)


def test_f01_python_single_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    src = work_dir / "hello.py"
    src.write_text('print("hello_py_e2e")\n', encoding="utf-8")
    res = run_cli(["hello.py"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "hello_py_e2e" in out
    assert "[ RUN ]" in out


def test_f01_bash_single_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    src = work_dir / "hello.sh"
    src.write_text('echo "hello_sh_e2e"\n', encoding="utf-8")
    res = run_cli(["hello.sh"])
    assert res.returncode == 0, _combined(res)
    assert "hello_sh_e2e" in res.stdout


def test_f01_rust_standalone_single_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    require_optional_tool("rustc")
    src = work_dir / "hello.rs"
    src.write_text('fn main() {\n    println!("hello_rs_e2e");\n}\n', encoding="utf-8")
    res = run_cli(["hello.rs"])
    assert res.returncode == 0, _combined(res)
    assert "hello_rs_e2e" in res.stdout


def test_f01_go_single_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    require_optional_tool("go")
    src = work_dir / "hello.go"
    src.write_text(
        'package main\nimport "fmt"\nfunc main() {\n    fmt.Println("hello_go_e2e")\n}\n',
        encoding="utf-8",
    )
    res = run_cli(["hello.go"])
    assert res.returncode == 0, _combined(res)
    assert "hello_go_e2e" in res.stdout


def test_f01_java_single_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    require_optional_tool("javac")
    require_optional_tool("java")
    src = work_dir / "HelloE2E.java"
    src.write_text(
        'public class HelloE2E {\n'
        '    public static void main(String[] args) {\n'
        '        System.out.println("hello_java_e2e");\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )
    res = run_cli(["HelloE2E.java"])
    assert res.returncode == 0, _combined(res)
    assert "hello_java_e2e" in res.stdout
    assert not (work_dir / "HelloE2E.class").exists()


def test_f01_ruby_single_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    require_optional_tool("ruby")
    src = work_dir / "hello.rb"
    src.write_text('puts "hello_rb_e2e"\n', encoding="utf-8")
    res = run_cli(["hello.rb"])
    assert res.returncode == 0, _combined(res)
    assert "hello_rb_e2e" in res.stdout


def test_f01_node_single_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    require_optional_tool("node")
    src = work_dir / "hello.js"
    src.write_text('console.log("hello_js_e2e");\n', encoding="utf-8")
    res = run_cli(["hello.js"])
    assert res.returncode == 0, _combined(res)
    assert "hello_js_e2e" in res.stdout


def test_f01_perl_single_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    require_optional_tool("perl")
    src = work_dir / "hello.pl"
    src.write_text('print "hello_pl_e2e\\n";\n', encoding="utf-8")
    res = run_cli(["hello.pl"])
    assert res.returncode == 0, _combined(res)
    assert "hello_pl_e2e" in res.stdout


def test_f01_lua_single_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    require_optional_tool("lua")
    src = work_dir / "hello.lua"
    src.write_text('print("hello_lua_e2e")\n', encoding="utf-8")
    res = run_cli(["hello.lua"])
    assert res.returncode == 0, _combined(res)
    assert "hello_lua_e2e" in res.stdout


# ---------------------------------------------------------------------------
# F02: C/C++ Shebang & Inline Directives
# ---------------------------------------------------------------------------


def test_f02_c_shebang_execution_and_workspace_clean(
    isolated_env: dict[str, Any],
    work_dir: Path,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    script = work_dir / "shebang_hello.c"
    script.write_text(
        "#!/usr/bin/env run\n"
        "#include <stdio.h>\n"
        'int main(void) { printf("shebang_c_ok\\n"); return 0; }\n',
        encoding="utf-8",
    )
    res = run_cli(["shebang_hello.c"])
    assert res.returncode == 0, _combined(res)
    assert "shebang_c_ok" in res.stdout
    # Shebang binaries live in XDG_CACHE_HOME/run/<hash>/bin, leaving workspace clean.
    assert not (work_dir / "shebang_hello.out").exists()
    cache_root = isolated_env["cache_home"] / "run"
    assert cache_root.exists()


def test_f02_extensionless_cpp_shebang_and_lang_directive(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    script = work_dir / "cpp_tool"
    script.write_text(
        "#!/usr/bin/env run\n"
        "// lang: cpp\n"
        "#include <iostream>\n"
        'int main() { std::cout << "extless_cpp_ok\\n"; return 0; }\n',
        encoding="utf-8",
    )
    res = run_cli(["cpp_tool"])
    assert res.returncode == 0, _combined(res)
    assert "extless_cpp_ok" in res.stdout


def test_f02_inline_directives_run_and_flags(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    script = work_dir / "directives.c"
    script.write_text(
        "#!/usr/bin/env run\n"
        "// run: -DFROM_RUN_DIR=42\n"
        "// flags: -DFROM_FLAGS_DIR=7\n"
        "#include <stdio.h>\n"
        'int main(void) { printf("vals=%d:%d\\n", FROM_RUN_DIR, FROM_FLAGS_DIR); return 0; }\n',
        encoding="utf-8",
    )
    res = run_cli(["directives.c"])
    assert res.returncode == 0, _combined(res)
    assert "vals=42:7" in res.stdout


def test_f02_shebang_line_preservation_in_compile_error(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    script = work_dir / "broken_line.c"
    script.write_text(
        "#!/usr/bin/env run\n"
        "#include <stdio.h>\n"
        "int main(void) {\n"
        "    int ok = 1;\n"
        "    int broken = ;\n"
        "    return ok;\n"
        "}\n",
        encoding="utf-8",
    )
    res = run_cli(["broken_line.c"])
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert ":5:" in combined


def test_f02_shebang_trailing_positional_arg_forwarding(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    script = work_dir / "echo_args.c"
    script.write_text(
        "#!/usr/bin/env run\n"
        "#include <stdio.h>\n"
        "int main(int argc, char **argv) {\n"
        '    for (int i = 1; i < argc; ++i) printf("arg[%d]=%s\\n", i, argv[i]);\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    res = run_cli(["echo_args.c", "alpha", "beta"])
    assert res.returncode == 0, _combined(res)
    assert "arg[1]=alpha" in res.stdout
    assert "arg[2]=beta" in res.stdout


def test_f02_shebang_persistent_cache_reuse(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    script = work_dir / "cached_shebang.c"
    script.write_text(
        "#!/usr/bin/env run\n"
        "#include <stdio.h>\n"
        'int main(void) { printf("cached_run_ok\\n"); return 0; }\n',
        encoding="utf-8",
    )
    first = run_cli(["cached_shebang.c"])
    assert first.returncode == 0, _combined(first)
    assert "cached_run_ok" in first.stdout

    second = run_cli(["cached_shebang.c"])
    assert second.returncode == 0, _combined(second)
    second_out = strip_ansi(second.stdout)
    assert "cached_run_ok" in second_out
    assert "[ INFO ] Using cached binary:" in second_out


# ---------------------------------------------------------------------------
# F03: Multi-File (-m) & Auto-Link (-L)
# ---------------------------------------------------------------------------


def test_f03_multi_file_c_cpm_main_detection_regardless_of_order(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "mathlib.h").write_text("int add_nums(int a, int b);\n", encoding="utf-8")
    (work_dir / "mathlib.c").write_text(
        '#include "mathlib.h"\nint add_nums(int a, int b) { return a + b; }\n',
        encoding="utf-8",
    )
    (work_dir / "entry.c").write_text(
        '#include <stdio.h>\n#include "mathlib.h"\n'
        'int main(void) { printf("sum=%d\\n", add_nums(19, 23)); return 0; }\n',
        encoding="utf-8",
    )
    # Pass helper file before main file to verify CPM main() detection.
    res = run_cli(["-m", "mathlib.c", "entry.c"])
    assert res.returncode == 0, _combined(res)
    assert "sum=42" in res.stdout


def test_f03_multi_file_parallel_jobs_flag(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "part_a.c").write_text("int val_a(void) { return 10; }\n", encoding="utf-8")
    (work_dir / "part_b.c").write_text("int val_b(void) { return 25; }\n", encoding="utf-8")
    (work_dir / "main.c").write_text(
        "#include <stdio.h>\n"
        "int val_a(void); int val_b(void);\n"
        'int main(void) { printf("total=%d\\n", val_a() + val_b()); return 0; }\n',
        encoding="utf-8",
    )
    res = run_cli(["-m", "-j", "2", "part_a.c", "part_b.c", "main.c"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "Compiling 3 files using 2 threads" in out
    assert "total=35" in out


def test_f03_auto_link_L_unlimited_and_depth_bounded(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    deep_proj = work_dir / "deep_proj"
    nested = deep_proj / "sub" / "nested"
    nested.mkdir(parents=True)
    (nested / "helper.h").write_text("int nested_val(void);\n", encoding="utf-8")
    (nested / "helper.c").write_text(
        '#include "helper.h"\nint nested_val(void) { return 77; }\n',
        encoding="utf-8",
    )
    (deep_proj / "main.c").write_text(
        '#include <stdio.h>\n#include "sub/nested/helper.h"\n'
        'int main(void) { printf("nested=%d\\n", nested_val()); return 0; }\n',
        encoding="utf-8",
    )
    res_unlimited = run_cli(["-L"], cwd=deep_proj)
    assert res_unlimited.returncode == 0, _combined(res_unlimited)
    assert "nested=77" in res_unlimited.stdout

    bounded_proj = work_dir / "bounded_proj"
    sub = bounded_proj / "sub"
    sub.mkdir(parents=True)
    # Invalid C inside sub/ must NOT be compiled when -L 0 restricts to root directory.
    (sub / "broken_sub.c").write_text("int bad = ;\n", encoding="utf-8")
    (bounded_proj / "root_lib.c").write_text("int root_val(void) { return 11; }\n", encoding="utf-8")
    (bounded_proj / "main.c").write_text(
        "#include <stdio.h>\nint root_val(void);\n"
        'int main(void) { printf("root=%d\\n", root_val()); return 0; }\n',
        encoding="utf-8",
    )
    res_depth0 = run_cli(["-L", "0"], cwd=bounded_proj)
    assert res_depth0.returncode == 0, _combined(res_depth0)
    assert "root=11" in res_depth0.stdout


def test_f03_core_exclusions_respected_during_L(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[core]\n"
        'exclude_dirs = ["ignored_dir"]\n'
        'exclude_files = ["skip_me.c"]\n'
        'exclude_extensions = [".bak", ".cc"]\n\n'
        "[[languages]]\n"
        'name = "bak_lang"\n'
        'extensions = [".bak"]\n'
        'compile = "cc -x c {files} -o {out}"\n'
        'type = "compiler"\n',
        encoding="utf-8",
    )
    ignored = work_dir / "ignored_dir"
    ignored.mkdir()
    (ignored / "broken.c").write_text("syntax error here;\n", encoding="utf-8")
    (work_dir / "skip_me.c").write_text("syntax error here;\n", encoding="utf-8")
    (work_dir / "ignored.bak").write_text("syntax error here;\n", encoding="utf-8")
    (work_dir / "broken.cc").write_text("syntax error here;\n", encoding="utf-8")
    (work_dir / "good.c").write_text("int good_fn(void) { return 88; }\n", encoding="utf-8")
    (work_dir / "main.c").write_text(
        "#include <stdio.h>\nint good_fn(void);\n"
        'int main(void) { printf("clean=%d\\n", good_fn()); return 0; }\n',
        encoding="utf-8",
    )
    res = run_cli(["-L"])
    assert res.returncode == 0, _combined(res)
    assert "clean=88" in res.stdout


def test_f03_multi_file_go(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    require_optional_tool("go")
    (work_dir / "util.go").write_text(
        'package main\nfunc helperMsg() string { return "go_multi_ok" }\n',
        encoding="utf-8",
    )
    (work_dir / "main.go").write_text(
        'package main\nimport "fmt"\nfunc main() { fmt.Println(helperMsg()) }\n',
        encoding="utf-8",
    )
    res = run_cli(["-m", "util.go", "main.go"])
    assert res.returncode == 0, _combined(res)
    assert "go_multi_ok" in res.stdout


def test_f03_multi_file_java(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    require_optional_tool("javac")
    require_optional_tool("java")
    (work_dir / "Greeter.java").write_text(
        'public class Greeter {\n    public static String msg() { return "java_multi_ok"; }\n}\n',
        encoding="utf-8",
    )
    (work_dir / "Entry.java").write_text(
        'public class Entry {\n'
        '    public static void main(String[] args) {\n'
        '        System.out.println(Greeter.msg());\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )
    res = run_cli(["-m", "Greeter.java", "Entry.java"])
    assert res.returncode == 0, _combined(res)
    assert "java_multi_ok" in res.stdout
