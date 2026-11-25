"""Tier 2 E2E boundary, corner-case, and negative tests: F01-F06."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable

import pytest

from tests.e2e.conftest import require_optional_tool, strip_ansi

pytestmark = pytest.mark.tier2


def _combined(res: subprocess.CompletedProcess[str]) -> str:
    return f"{res.stdout}\n{res.stderr}"


# ---------------------------------------------------------------------------
# F01: Single-File Built-in Languages Boundaries (7 tests)
# ---------------------------------------------------------------------------


def test_f01_empty_c_and_py_files(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "empty.c").write_text("", encoding="utf-8")
    (work_dir / "empty.py").write_text("", encoding="utf-8")

    py_res = run_cli(["empty.py"])
    assert py_res.returncode == 0, _combined(py_res)
    assert "[ RUN ]" in strip_ansi(py_res.stdout)

    c_res = run_cli(["empty.c"])
    c_combined = strip_ansi(_combined(c_res))
    assert "[ ERROR ]" in c_combined
    assert "Compilation failed" in c_combined
    expected_bin = work_dir / ("empty.exe" if os.name == "nt" else "empty.out")
    assert not expected_bin.exists()


def test_f01_missing_files_c_py_and_extensionless(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    c_res = run_cli(["missing.c"])
    c_combined = strip_ansi(_combined(c_res))
    assert "[ ERROR ]" in c_combined
    assert "Compilation failed" in c_combined

    py_res = run_cli(["missing.py"])
    py_combined = strip_ansi(_combined(py_res))
    assert "[ ERROR ]" in py_combined
    assert "Execution failed" in py_combined

    no_ext_res = run_cli(["missing_no_ext"])
    no_ext_combined = strip_ansi(_combined(no_ext_res))
    assert "[ ERROR ]" in no_ext_combined
    assert "Unsupported extension" in no_ext_combined


def test_f01_unsupported_extension(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "unknown.xyz").write_text("hello\n", encoding="utf-8")
    res = run_cli(["unknown.xyz"])
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert "Unsupported extension: .xyz" in combined


def test_f01_hyphenated_filenames_c_py_and_bare_flag_error(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "-main.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("HYPHEN_C_OK\\n"); return 0; }\n',
        encoding="utf-8",
    )
    (work_dir / "-c.py").write_text('print("HYPHEN_PY_OK")\n', encoding="utf-8")

    c_res = run_cli(["./-main.c"])
    assert c_res.returncode == 0, _combined(c_res)
    assert "HYPHEN_C_OK" in c_res.stdout

    py_res = run_cli(["./-c.py"])
    assert py_res.returncode == 0, _combined(py_res)
    assert "HYPHEN_PY_OK" in py_res.stdout

    auto_dir = work_dir / "auto_hyphen"
    auto_dir.mkdir()
    (auto_dir / "-auto.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("AUTO_HYPHEN_OK\\n"); return 0; }\n',
        encoding="utf-8",
    )
    l_res = run_cli(["-L"], cwd=auto_dir)
    assert l_res.returncode == 0, _combined(l_res)
    assert "AUTO_HYPHEN_OK" in l_res.stdout

    bare_res = run_cli(["-c.py"])
    assert bare_res.returncode == 2


def test_f01_hyphenated_java_main_class_rejected(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    require_optional_tool("javac")
    require_optional_tool("java")
    (work_dir / "-Main.java").write_text(
        "class Valid {\n"
        "    public static void main(String[] args) {\n"
        '        System.out.println("SHOULD_NOT_RUN");\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    res = run_cli(["./-Main.java"])
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert "Invalid main class name: -Main" in combined
    assert "SHOULD_NOT_RUN" not in combined


def test_f01_spaces_in_dirs_and_filenames(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    spaced = work_dir / "dir with spaces"
    spaced.mkdir()
    c_file = spaced / "hello world.c"
    c_file.write_text(
        '#include <stdio.h>\nint main(void) { printf("SPACED_C_OK\\n"); return 0; }\n',
        encoding="utf-8",
    )
    py_file = spaced / "hello world.py"
    py_file.write_text('print("SPACED_PY_OK")\n', encoding="utf-8")

    c_res = run_cli(["--out-dir", "out dir", "dir with spaces/hello world.c"])
    assert c_res.returncode == 0, _combined(c_res)
    assert "SPACED_C_OK" in c_res.stdout
    out_bin = work_dir / "out dir" / ("hello world.exe" if os.name == "nt" else "hello world.out")
    assert not out_bin.exists()

    py_res = run_cli(["dir with spaces/hello world.py"])
    assert py_res.returncode == 0, _combined(py_res)
    assert "SPACED_PY_OK" in py_res.stdout


def test_f01_multi_positional_syntax_error_recovery(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "bad.c").write_text("int broken = ;\n", encoding="utf-8")
    (work_dir / "good.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("GOOD_OK\\n"); return 0; }\n',
        encoding="utf-8",
    )
    res = run_cli(["bad.c", "good.c"])
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert "bad.c" in combined
    assert "GOOD_OK" in res.stdout


# ---------------------------------------------------------------------------
# F02: C/C++ Shebang & Inline Directives Boundaries (7 tests)
# ---------------------------------------------------------------------------


def test_f02_shebang_without_trailing_newline(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    only_shebang = work_dir / "only_shebang"
    only_shebang.write_text("#!/usr/bin/env run", encoding="utf-8")
    res_empty = run_cli(["only_shebang"])
    combined_empty = strip_ansi(_combined(res_empty))
    assert "[ ERROR ]" in combined_empty
    assert "Compilation failed" in combined_empty

    no_eof_nl = work_dir / "no_eof_nl"
    no_eof_nl.write_text(
        "#!/usr/bin/env run\n"
        "#include <stdio.h>\n"
        'int main(void) { printf("NO_EOF_NL_OK\\n"); return 0; }',
        encoding="utf-8",
    )
    res_ok = run_cli(["no_eof_nl"])
    assert res_ok.returncode == 0, _combined(res_ok)
    assert "NO_EOF_NL_OK" in res_ok.stdout


def test_f02_malformed_and_beyond_limit_inline_directives(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    padding = "\n".join(f"// pad line {i}" for i in range(1, 32))
    script = work_dir / "directives.c"
    script.write_text(
        "#!/usr/bin/env run\n"
        '// run: "unclosed quote\n'
        "// run:\n"
        "// run: -DOK_VAL=5\n"
        f"{padding}\n"
        "// flags: -DBEYOND_30=1\n"
        "#include <stdio.h>\n"
        "int main(void) {\n"
        "#ifdef BEYOND_30\n"
        '    printf("BEYOND_ACTIVE\\n");\n'
        "#else\n"
        '    printf("OK=%d\\n", OK_VAL);\n'
        "#endif\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    res = run_cli(["directives.c"])
    assert res.returncode == 0, _combined(res)
    assert "OK=5" in res.stdout
    assert "BEYOND_ACTIVE" not in res.stdout


def test_f02_extensionless_cpp_heuristics_vs_c_fallback(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    cpp_iostream = work_dir / "ext_iostream"
    cpp_iostream.write_text(
        "#!/usr/bin/env run\n"
        "#include <iostream>\n"
        'int main() { std::cout << "CPP_IOSTREAM_OK\\n"; return 0; }\n',
        encoding="utf-8",
    )
    r1 = run_cli(["ext_iostream"])
    assert r1.returncode == 0, _combined(r1)
    assert "CPP_IOSTREAM_OK" in r1.stdout

    cpp_lang = work_dir / "ext_lang_cpp"
    cpp_lang.write_text(
        "#!/usr/bin/env run\n"
        "// lang: cpp\n"
        "#include <cstdio>\n"
        "int main() {\n"
        "    auto fn = []() -> int { return 42; };\n"
        "    bool ok = (fn() == 42);\n"
        '    if (ok) std::printf("CPP_LANG_OK\\n");\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    r2 = run_cli(["ext_lang_cpp"])
    assert r2.returncode == 0, _combined(r2)
    assert "CPP_LANG_OK" in r2.stdout

    c_fallback = work_dir / "ext_c_plain"
    c_fallback.write_text(
        "#!/usr/bin/env run\n"
        "#include <stdio.h>\n"
        'int main(void) { printf("C_FALLBACK_OK\\n"); return 0; }\n',
        encoding="utf-8",
    )
    r3 = run_cli(["ext_c_plain"])
    assert r3.returncode == 0, _combined(r3)
    assert "C_FALLBACK_OK" in r3.stdout


def test_f02_shebang_line2_and_filename_in_compiler_error(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "err_line2.c").write_text(
        "#!/usr/bin/env run\nint broken = ;\n",
        encoding="utf-8",
    )
    res = run_cli(["err_line2.c"])
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert "err_line2.c" in res.stderr
    assert ":2:" in res.stderr


def test_f02_shebang_spaces_in_path_and_args(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    sub = work_dir / "sub dir"
    sub.mkdir()
    tool = sub / "my tool"
    tool.write_text(
        "#!/usr/bin/env run\n"
        "#include <stdio.h>\n"
        "int main(int argc, char **argv) {\n"
        "    for (int i = 1; i < argc; ++i) {\n"
        '        printf("ARG%d=[%s]\\n", i, argv[i]);\n'
        "    }\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    res = run_cli(["sub dir/my tool", "hello world", "a\tb"])
    assert res.returncode == 0, _combined(res)
    assert "ARG1=[hello world]" in res.stdout
    assert "ARG2=[a\tb]" in res.stdout
    assert not list(sub.glob("*.out"))
    assert not list(sub.glob("*.exe"))


def test_f02_shebang_transitive_header_cache_invalidation(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "b.h").write_text("#define TRANSITIVE_VAL 1\n", encoding="utf-8")
    (work_dir / "a.h").write_text('#include "b.h"\n', encoding="utf-8")
    (work_dir / "app").write_text(
        "#!/usr/bin/env run\n"
        "#include <stdio.h>\n"
        '#include "a.h"\n'
        'int main(void) { printf("VAL=%d\\n", TRANSITIVE_VAL); return 0; }\n',
        encoding="utf-8",
    )

    r1 = run_cli(["app"])
    assert r1.returncode == 0, _combined(r1)
    assert "VAL=1" in r1.stdout

    (work_dir / "b.h").write_text("#define TRANSITIVE_VAL 2\n", encoding="utf-8")
    r2 = run_cli(["app"])
    assert r2.returncode == 0, _combined(r2)
    assert "VAL=2" in r2.stdout
    assert "Using cached binary:" not in strip_ansi(r2.stdout)


def test_f02_shebang_commented_include_and_stem_collision(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "unused.h").write_text("#define UNUSED_ONE 1\n", encoding="utf-8")
    (work_dir / "tool.c").write_text(
        "#!/usr/bin/env run\n"
        '#include <stdio.h>\n'
        '// #include "unused.h"\n'
        '/* #include "ghost.h" */\n'
        'int main(void) { printf("STEM_C_OK\\n"); return 0; }\n'
        '// #include "unused.h"',
        encoding="utf-8",
    )
    (work_dir / "tool.cpp").write_text(
        "#!/usr/bin/env run\n"
        "#include <iostream>\n"
        'int main() { std::cout << "STEM_CPP_OK\\n"; return 0; }\n',
        encoding="utf-8",
    )

    r1_c = run_cli(["tool.c"])
    assert r1_c.returncode == 0, _combined(r1_c)
    assert "STEM_C_OK" in r1_c.stdout

    # Modifying commented-out header (including EOF line without \n) must NOT bust cache.
    (work_dir / "unused.h").write_text("#define UNUSED_ONE 999\n", encoding="utf-8")
    r2_c = run_cli(["tool.c"])
    assert r2_c.returncode == 0, _combined(r2_c)
    assert "[ INFO ] Using cached binary:" in strip_ansi(r2_c.stdout)
    assert "STEM_C_OK" in r2_c.stdout

    r_cpp = run_cli(["tool.cpp"])
    assert r_cpp.returncode == 0, _combined(r_cpp)
    assert "STEM_CPP_OK" in r_cpp.stdout
    assert "STEM_C_OK" not in r_cpp.stdout


# ---------------------------------------------------------------------------
# F03: Multi-File (-m) & Auto-Link (-L) Boundaries (7 tests)
# ---------------------------------------------------------------------------


def test_f03_multi_no_main_error(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "a.c").write_text("int helper_a(void) { return 1; }\n", encoding="utf-8")
    (work_dir / "b.c").write_text("int helper_b(void) { return 2; }\n", encoding="utf-8")

    res = run_cli(["-m", "a.c", "b.c"])
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert "Multi-file compilation failed:" in combined
    assert "Could not find a 'main' function" in combined


def test_f03_multi_main_last_file_and_void_main(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "a.c").write_text("int fa(void) { return 10; }\n", encoding="utf-8")
    (work_dir / "b.c").write_text("int fb(void) { return 20; }\n", encoding="utf-8")
    (work_dir / "z_last.c").write_text(
        "#include <stdio.h>\n"
        "#include <stdlib.h>\n"
        "int fa(void); int fb(void);\n"
        'void main(void) { printf("VOID_MAIN_OK=%d\\n", fa() + fb()); exit(0); }\n',
        encoding="utf-8",
    )

    res = run_cli(["-m", "a.c", "b.c", "z_last.c"])
    assert res.returncode == 0, _combined(res)
    assert "VOID_MAIN_OK=30" in res.stdout


def test_f03_multi_auto_main_cpp(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "helper.cpp").write_text("int compute_v() { return 77; }\n", encoding="utf-8")
    (work_dir / "entry.cpp").write_text(
        "#include <iostream>\n"
        "int compute_v();\n"
        'auto main() -> int { std::cout << "AUTO_MAIN_OK=" << compute_v() << "\\n"; return 0; }\n',
        encoding="utf-8",
    )

    res = run_cli(["-m", "helper.cpp", "entry.cpp"])
    assert res.returncode == 0, _combined(res)
    assert "AUTO_MAIN_OK=77" in res.stdout


def test_f03_multi_commented_main_ignored(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "fake.c").write_text(
        "// int main() { return 1; }\n"
        "/* int main() { return 2; } */\n"
        "int helper_val(void) { return 5; }\n",
        encoding="utf-8",
    )
    (work_dir / "only_helper.c").write_text("int other_val(void) { return 6; }\n", encoding="utf-8")
    (work_dir / "real.c").write_text(
        "#include <stdio.h>\n"
        "int helper_val(void);\n"
        'int main(void) { printf("REAL_MAIN_OK=%d\\n", helper_val()); return 0; }\n',
        encoding="utf-8",
    )

    ok_res = run_cli(["-m", "fake.c", "real.c"])
    assert ok_res.returncode == 0, _combined(ok_res)
    assert "REAL_MAIN_OK=5" in ok_res.stdout

    err_res = run_cli(["-m", "fake.c", "only_helper.c"])
    err_combined = strip_ansi(_combined(err_res))
    assert "[ ERROR ]" in err_combined
    assert "Could not find a 'main' function" in err_combined


def test_f03_auto_link_depth_0_vs_1_boundary(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    proj = work_dir / "depth_proj"
    (proj / "l1" / "l2").mkdir(parents=True)
    (proj / "root.c").write_text(
        "#include <stdio.h>\n"
        "#ifdef USE_L1\n"
        "int l1_val(void);\n"
        "#endif\n"
        "int main(void) {\n"
        "#ifdef USE_L1\n"
        '    printf("DEPTH1=%d\\n", l1_val());\n'
        "#else\n"
        '    printf("DEPTH0_OK\\n");\n'
        "#endif\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (proj / "l1" / "f1.c").write_text("int l1_val(void) { return 99; }\n", encoding="utf-8")
    (proj / "l1" / "l2" / "broken.c").write_text("SYNTAX ERROR IN L2;\n", encoding="utf-8")

    d0 = run_cli(["-L", "0"], cwd=proj)
    assert d0.returncode == 0, _combined(d0)
    assert "DEPTH0_OK" in d0.stdout

    d1 = run_cli(["-L", "1", "--flags", "-DUSE_L1=1"], cwd=proj)
    assert d1.returncode == 0, _combined(d1)
    assert "DEPTH1=99" in d1.stdout

    empty_root = work_dir / "empty_root"
    (empty_root / "l1").mkdir(parents=True)
    (empty_root / "l1" / "only.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    empty_res = run_cli(["-L", "0"], cwd=empty_root)
    assert empty_res.returncode == 1
    assert "No supported source files found via -L auto-search" in strip_ansi(_combined(empty_res))


def test_f03_core_exclusions_glob_and_single_skip(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[core]\n"
        'exclude_extensions = ["bak", ".tmp*"]\n'
        'exclude_files = ["sub/mock_*.c", "*.skip.c"]\n'
        'exclude_dirs = ["vendor/ignored"]\n',
        encoding="utf-8",
    )
    (work_dir / "main.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("EXCL_OK\\n"); return 0; }\n',
        encoding="utf-8",
    )
    (work_dir / "dead.skip.c").write_text("BROKEN SYNTAX;\n", encoding="utf-8")
    (work_dir / "junk.bak").write_text("BROKEN;\n", encoding="utf-8")
    (work_dir / "scratch.tmp1").write_text("BROKEN;\n", encoding="utf-8")
    (work_dir / "sub").mkdir()
    (work_dir / "sub" / "mock_1.c").write_text("BROKEN MOCK;\n", encoding="utf-8")
    (work_dir / "vendor" / "ignored").mkdir(parents=True)
    (work_dir / "vendor" / "ignored" / "bad.c").write_text("BROKEN VENDOR;\n", encoding="utf-8")

    res_l = run_cli(["-L"])
    assert res_l.returncode == 0, _combined(res_l)
    assert "EXCL_OK" in res_l.stdout

    res_skip = run_cli(["sub/mock_1.c"])
    assert res_skip.returncode == 0, _combined(res_skip)
    skip_out = strip_ansi(res_skip.stdout)
    assert "[ SKIP ]" in skip_out
    assert "exclude files" in skip_out


def test_f03_multi_unsupported_files_and_invalid_core_toml(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "a.txt").write_text("hello\n", encoding="utf-8")
    (work_dir / "b.txt").write_text("world\n", encoding="utf-8")
    res_unsup = run_cli(["-m", "a.txt", "b.txt"])
    unsup_combined = strip_ansi(_combined(res_unsup))
    assert "[ ERROR ]" in unsup_combined
    assert "No supported files found for multi-compile" in unsup_combined

    (work_dir / "Run.toml").write_text('[core]\nexclude_files = "not_a_list"\n', encoding="utf-8")
    (work_dir / "ok.py").write_text('print("ok")\n', encoding="utf-8")
    res_bad_cfg = run_cli(["ok.py"])
    assert res_bad_cfg.returncode == 1
    bad_combined = strip_ansi(_combined(res_bad_cfg))
    assert "[ ERROR ]" in bad_combined
    assert "core.exclude_files" in bad_combined


# ---------------------------------------------------------------------------
# F04: Tasks ([tasks]) & Projects ([projects]) Boundaries (6 tests)
# ---------------------------------------------------------------------------


def test_f04_missing_task_falls_through_cleanly(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        '[tasks]\nknown = "python3 -c \\"print(\'ok\')\\""\n',
        encoding="utf-8",
    )
    res = run_cli(["unknown_task"])
    combined = strip_ansi(_combined(res))
    assert "[ ERROR ]" in combined
    assert "unknown_task" in combined
    assert "Unsupported extension" in combined


def test_f04_invalid_task_toml_types(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text('tasks = "bad"\n', encoding="utf-8")
    r1 = run_cli(["anything"])
    assert r1.returncode == 1
    assert "[ ERROR ]" in strip_ansi(_combined(r1))

    (work_dir / "Run.toml").write_text("[tasks.no_cmd]\nsandbox = true\n", encoding="utf-8")
    r2 = run_cli(["no_cmd"])
    assert r2.returncode == 1
    assert "[ ERROR ]" in strip_ansi(_combined(r2))

    (work_dir / "Run.toml").write_text("[tasks]\nbad_num = 123\n", encoding="utf-8")
    r3 = run_cli(["bad_num"])
    assert r3.returncode == 1
    assert "[ ERROR ]" in strip_ansi(_combined(r3))


def test_f04_compound_task_and_short_circuit_vs_semicolon(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[tasks]\n"
        'fail_and = "python3 -c \\"import sys; sys.exit(1)\\" && python3 -c \\"print(\'NEVER_RUN\')\\""\n'
        'semi_ok = "python3 -c \\"print(\'S1\')\\" ; python3 -c \\"print(\'S2\')\\""\n',
        encoding="utf-8",
    )

    r_fail = run_cli(["fail_and"])
    assert r_fail.returncode == 1
    non_task_lines = [
        line for line in strip_ansi(_combined(r_fail)).splitlines() if "[ TASK ]" not in line
    ]
    assert all("NEVER_RUN" not in line for line in non_task_lines)
    assert "[ ERROR ]" in strip_ansi(_combined(r_fail))

    r_semi = run_cli(["semi_ok"])
    assert r_semi.returncode == 0, _combined(r_semi)
    assert r_semi.stdout.index("S1") < r_semi.stdout.index("S2")


def test_f04_task_quoted_operators_not_split(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[tasks]\n"
        "q = \"python3 -c \\\"import sys; print('OUT:' + sys.argv[1])\\\" 'hello && world ; ok'\"\n",
        encoding="utf-8",
    )
    res = run_cli(["q"])
    assert res.returncode == 0, _combined(res)
    assert "OUT:hello && world ; ok" in res.stdout


def test_f04_project_glob_manifest_and_errors(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    glob_dir = work_dir / "glob_proj"
    glob_dir.mkdir()
    (glob_dir / "Run.toml").write_text(
        "[projects.dotnet]\n"
        'file = "*.sln"\n'
        'command = "python3 -c \\"import sys; print(\'SLN:\' + sys.argv[1])\\" ${file}"\n',
        encoding="utf-8",
    )
    (glob_dir / "SolutionB.sln").write_text("", encoding="utf-8")
    (glob_dir / "SolutionA.sln").write_text("", encoding="utf-8")
    r_glob = run_cli([], cwd=glob_dir)
    assert r_glob.returncode == 0, _combined(r_glob)
    glob_out = strip_ansi(r_glob.stdout)
    assert "Multiple manifest files matched '*.sln'" in glob_out
    assert "SLN:" in glob_out
    assert "SolutionA.sln" in glob_out

    build_fail_dir = work_dir / "build_fail_proj"
    build_fail_dir.mkdir()
    (build_fail_dir / "Run.toml").write_text(
        "[projects.twostep]\n"
        'file = "marker.mk"\n'
        'build = "python3 -c \\"import sys; sys.exit(1)\\""\n'
        'run = "python3 -c \\"print(\'SHOULD_NOT_RUN\')\\""\n',
        encoding="utf-8",
    )
    (build_fail_dir / "marker.mk").write_text("", encoding="utf-8")
    r_bf = run_cli([], cwd=build_fail_dir)
    assert r_bf.returncode == 1
    assert "SHOULD_NOT_RUN" not in _combined(r_bf)
    assert "[ ERROR ]" in strip_ansi(_combined(r_bf))

    inc_dir = work_dir / "incomplete_proj"
    inc_dir.mkdir()
    (inc_dir / "Run.toml").write_text(
        '[projects.incomplete]\nfile = "x.mk"\n',
        encoding="utf-8",
    )
    (inc_dir / "x.mk").write_text("", encoding="utf-8")
    r_inc = run_cli([], cwd=inc_dir)
    assert r_inc.returncode == 1
    inc_combined = strip_ansi(_combined(r_inc))
    assert "[ ERROR ]" in inc_combined
    assert "must define 'command' or both 'build' and 'run'" in inc_combined


def test_f04_directory_nonexistent_and_file_target_exit_1(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    r_missing = run_cli(["--directory", "missing_dir"])
    assert r_missing.returncode == 1
    assert "[ ERROR ] Specified directory does not exist:" in strip_ansi(_combined(r_missing))

    regular_file = work_dir / "some_file.txt"
    regular_file.write_text("not a dir\n", encoding="utf-8")
    r_file = run_cli(["--directory", str(regular_file)])
    assert r_file.returncode == 1
    assert "[ ERROR ] Specified directory does not exist:" in strip_ansi(_combined(r_file))


# ---------------------------------------------------------------------------
# F05: Stdin, Args, Expect & Preprocessor Boundaries (6 tests)
# ---------------------------------------------------------------------------


def test_f05_stdin_missing_file_and_directory_error(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "echo.py").write_text('import sys\nprint(sys.stdin.read())\n', encoding="utf-8")
    (work_dir / "subdir").mkdir()

    r_missing = run_cli(["echo.py", "--stdin-file", "no_such.txt"])
    missing_combined = strip_ansi(_combined(r_missing))
    assert "[ ERROR ]" in missing_combined
    assert "Failed to open stdin file" in missing_combined

    r_dir = run_cli(["echo.py", "-i", "subdir"])
    dir_combined = strip_ansi(_combined(r_dir))
    assert "[ ERROR ]" in dir_combined
    assert "Failed to open stdin file" in dir_combined


def test_f05_unconsumed_large_stdin_no_broken_pipe(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "fast_exit.py").write_text('print("FAST_OK")\n', encoding="utf-8")
    payload = "X" * (1536 * 1024)
    big_in = work_dir / "big.in"
    big_in.write_text(payload, encoding="utf-8")

    r_file = run_cli(["fast_exit.py", "-i", "big.in"])
    assert r_file.returncode == 0, _combined(r_file)
    assert "FAST_OK" in r_file.stdout
    assert "BrokenPipe" not in r_file.stderr

    r_pipe = run_cli(["fast_exit.py", "-i", "-"], stdin_text=payload)
    assert r_pipe.returncode == 0, _combined(r_pipe)
    assert "FAST_OK" in r_pipe.stdout
    assert "BrokenPipe" not in r_pipe.stderr


def test_f05_expect_whitespace_stripping_and_outside_workspace(
    isolated_env: dict[str, object],
    work_dir: Path,
    run_cli: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    (work_dir / "prog.py").write_text('print("  hi  \\n")\n', encoding="utf-8")
    (work_dir / "exp.txt").write_text("\n\thi\t \n\n", encoding="utf-8")

    r_ws = run_cli(["prog.py", "--expect", "exp.txt"])
    assert r_ws.returncode == 0, _combined(r_ws)
    assert "[ PASS ] Output matches" in strip_ansi(r_ws.stdout)

    outside_dir = work_dir.parent / "outside_expect_dir"
    outside_dir.mkdir(exist_ok=True)
    outside_exp = outside_dir / "exp.txt"
    outside_exp.write_text("hi\n", encoding="utf-8")

    r_deny = run_cli(["prog.py", "--expect", str(outside_exp)])
    deny_combined = strip_ansi(_combined(r_deny))
    assert "[ ERROR ]" in deny_combined
    assert "outside the workspace" in deny_combined

    r_force = run_cli(["-f", "prog.py", "--expect", str(outside_exp)])
    assert r_force.returncode == 0, _combined(r_force)
    force_out = strip_ansi(r_force.stdout)
    assert "[ WARN ]" in force_out
    assert "--force" in force_out
    assert "[ PASS ] Output matches" in force_out


def test_f05_expect_large_diff_safety(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    lines_actual = "\n".join(f"actual_line_{i:05d}" for i in range(5000))
    lines_expected = "\n".join(f"expected_line_{i:05d}" for i in range(5000))
    (work_dir / "gen.py").write_text(
        f'print("""{lines_actual}""")\n',
        encoding="utf-8",
    )
    (work_dir / "exp_large.txt").write_text(lines_expected + "\n", encoding="utf-8")

    res = run_cli(["gen.py", "--expect", "exp_large.txt"], timeout=15.0)
    out = strip_ansi(res.stdout)
    assert "[ FAIL ] Output mismatch with" in out
    assert "--- Differences ---" in out
    assert "-------------------" in out


def test_f05_preprocessor_equals_flags_and_arguments(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "macro.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("MAC=%d\\n", MAC); return 0; }\n',
        encoding="utf-8",
    )
    r_flags = run_cli(["macro.c", "--flags=-DMAC=7"])
    assert r_flags.returncode == 0, _combined(r_flags)
    assert "MAC=7" in r_flags.stdout

    (work_dir / "args.py").write_text(
        'import sys\nprint("ARGV:" + "|".join(sys.argv[1:]))\n',
        encoding="utf-8",
    )
    r_eq = run_cli(["args.py", "--argument=--port=8080"])
    assert r_eq.returncode == 0, _combined(r_eq)
    assert "ARGV:--port=8080" in r_eq.stdout

    r_merge = run_cli(["args.py", "--argument", 'a "b c"', "--", "d e"])
    assert r_merge.returncode == 0, _combined(r_merge)
    assert "ARGV:a|b c|d e" in r_merge.stdout


def test_f05_preprocessor_i_dash_before_flag_and_no_hijack(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "reader.py").write_text(
        'import sys\nprint("GOT:" + sys.stdin.read().strip())\n',
        encoding="utf-8",
    )
    r_iv = run_cli(["-i", "-v", "reader.py"], stdin_text="PIPED\n")
    assert r_iv.returncode == 0, _combined(r_iv)
    iv_out = strip_ansi(r_iv.stdout)
    assert "[ DEBUG ] Debug logging enabled" in iv_out
    assert "GOT:PIPED" in iv_out

    (work_dir / "input.cpp").write_text("INVALID C++ NOT SUPPOSED TO COMPILE;\n", encoding="utf-8")
    r_no_hijack = run_cli(["-i", "input.cpp"], stdin_text="")
    assert r_no_hijack.returncode == 0, _combined(r_no_hijack)
    no_hijack_out = strip_ansi(r_no_hijack.stdout)
    assert "[ INPUT ] No file given" in no_hijack_out
    assert "[ COMPILE ]" not in no_hijack_out


# ---------------------------------------------------------------------------
# F06: Batch Testcases (--test-dir) & Metrics Boundaries (5 tests)
# ---------------------------------------------------------------------------


def test_f06_test_dir_empty_unmatched_missing_and_no_file(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "echo.py").write_text(
        "import sys\nprint(sys.stdin.read().strip())\n",
        encoding="utf-8",
    )

    empty_dir = work_dir / "empty_cases"
    empty_dir.mkdir()
    r_empty = run_cli(["echo.py", "--test-dir", str(empty_dir)])
    assert r_empty.returncode == 1
    assert "[ WARN ] No matching test pairs found in" in strip_ansi(_combined(r_empty))

    unmatched_dir = work_dir / "unmatched_cases"
    unmatched_dir.mkdir()
    (unmatched_dir / "orphan_a.in").write_text("1\n", encoding="utf-8")
    (unmatched_dir / "orphan_b.out").write_text("1\n", encoding="utf-8")
    (unmatched_dir / "readme.txt").write_text("notes\n", encoding="utf-8")
    r_unmatched = run_cli(["echo.py", "--test-dir", str(unmatched_dir)])
    assert r_unmatched.returncode == 1
    assert "[ WARN ] No matching test pairs found in" in strip_ansi(_combined(r_unmatched))

    r_missing = run_cli(["echo.py", "--test-dir", "no_such_dir"])
    assert r_missing.returncode == 1
    assert "[ WARN ] No matching test pairs found in" in strip_ansi(_combined(r_missing))

    valid_dir = work_dir / "valid_cases"
    valid_dir.mkdir()
    (valid_dir / "1.in").write_text("ok\n", encoding="utf-8")
    (valid_dir / "1.out").write_text("ok\n", encoding="utf-8")
    (valid_dir / "orphan.in").write_text("ignored\n", encoding="utf-8")
    (valid_dir / "unmatched_other.out").write_text("ignored\n", encoding="utf-8")
    (valid_dir / "notes.txt").write_text("ignored\n", encoding="utf-8")

    no_src_dir = work_dir / "isolated_no_src"
    no_src_dir.mkdir()
    r_no_src = run_cli(["--test-dir", str(valid_dir)], cwd=no_src_dir)
    assert r_no_src.returncode == 1
    assert "[ ERROR ] A source file must be specified with --test-dir" in strip_ansi(
        _combined(r_no_src)
    )

    r_valid = run_cli(["echo.py", "--test-dir", str(valid_dir)])
    assert r_valid.returncode == 0, _combined(r_valid)
    valid_out = strip_ansi(r_valid.stdout)
    assert "Discovered 1 test case(s)" in valid_out
    assert "Passed: 1/1 (100.0%)" in valid_out


def test_f06_test_dir_natural_sort_in_underscore_txt(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "echo.py").write_text(
        "import sys\nprint(sys.stdin.read().strip())\n",
        encoding="utf-8",
    )
    cases = work_dir / "cases"
    cases.mkdir()
    for idx in ("10", "2", "1"):
        (cases / f"in_{idx}.txt").write_text(f"v{idx}\n", encoding="utf-8")
        (cases / f"out_{idx}.txt").write_text(f"v{idx}\n", encoding="utf-8")
    (cases / "input5.txt").write_text("v5\n", encoding="utf-8")
    (cases / "ans5.txt").write_text("v5\n", encoding="utf-8")
    (cases / "in.txt").write_text("v0\n", encoding="utf-8")
    (cases / "out.txt").write_text("v0\n", encoding="utf-8")

    res = run_cli(["echo.py", "--test-dir", "cases"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "Discovered 5 test case(s)" in out
    assert "Passed: 5/5 (100.0%)" in out
    p1 = out.index("out_1.txt")
    p2 = out.index("out_2.txt")
    p10 = out.index("out_10.txt")
    assert p1 < p2 < p10


def test_f06_test_dir_empty_in_and_out_files(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "noop.py").write_text("import sys\n_ = sys.stdin.read()\n", encoding="utf-8")
    cases = work_dir / "cases"
    cases.mkdir()
    (cases / "01.in").write_text("", encoding="utf-8")
    (cases / "01.out").write_text("", encoding="utf-8")

    res = run_cli(["noop.py", "--test-dir", "cases"])
    assert res.returncode == 0, _combined(res)
    assert "Passed: 1/1 (100.0%)" in strip_ansi(res.stdout)


def test_f06_test_dir_mixed_pass_fail_timeout_crash_exit_1(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "mixed.py").write_text(
        "import sys, time\n"
        "mode = sys.stdin.read().strip()\n"
        "if mode == 'PASS':\n"
        "    print('OK')\n"
        "elif mode == 'FAIL':\n"
        "    print('WRONG')\n"
        "elif mode == 'TIMEOUT':\n"
        "    time.sleep(5)\n"
        "elif mode == 'CRASH':\n"
        "    sys.exit(2)\n",
        encoding="utf-8",
    )
    cases = work_dir / "cases"
    cases.mkdir()
    specs = [
        ("01", "PASS\n", "OK\n"),
        ("02", "FAIL\n", "EXPECTED\n"),
        ("03", "TIMEOUT\n", "OK\n"),
        ("04", "CRASH\n", "OK\n"),
        ("05", "PASS\n", "OK\n"),
    ]
    for name, inp, exp in specs:
        (cases / f"{name}.in").write_text(inp, encoding="utf-8")
        (cases / f"{name}.out").write_text(exp, encoding="utf-8")

    res = run_cli(["mixed.py", "--test-dir", "cases", "--timeout", "0.5"], timeout=15.0)
    assert res.returncode == 1
    out = strip_ansi(_combined(res))
    assert "[ PASS ]" in out
    assert "[ FAIL ]" in out
    assert "[ ERROR ]" in out
    assert "Passed: 2/5 (40.0%)" in out


def test_f06_mem_isolation_alternating_heavy_light_testcases(
    work_dir: Path, run_cli: Callable[..., subprocess.CompletedProcess[str]]
) -> None:
    (work_dir / "alloc.py").write_text(
        "import sys\n"
        "mb = int(sys.stdin.read().strip())\n"
        "b = bytearray(mb * 1024 * 1024)\n"
        "for i in range(0, len(b), 4096):\n"
        "    b[i] = 1\n"
        "print(mb)\n",
        encoding="utf-8",
    )
    cases = work_dir / "cases"
    cases.mkdir()
    sizes = [1, 50, 1, 60, 1, 45]
    for idx, mb in enumerate(sizes, start=1):
        (cases / f"{idx:02d}.in").write_text(f"{mb}\n", encoding="utf-8")
        (cases / f"{idx:02d}.out").write_text(f"{mb}\n", encoding="utf-8")

    res = run_cli(["alloc.py", "--test-dir", "cases", "-M", "--no-color"])
    assert res.returncode == 0, _combined(res)
    out = strip_ansi(res.stdout)
    assert "Passed: 6/6 (100.0%)" in out

    matches = re.findall(r"Peak Memory:\s*([\d.]+)\s*(MB|KB)", out)
    assert len(matches) == 6, f"Expected 6 Peak Memory lines, got {matches!r} in:\n{out}"

    rss_mb = [
        float(val) / 1024.0 if unit == "KB" else float(val)
        for val, unit in matches
    ]
    for idx, (target_mb, measured_mb) in enumerate(zip(sizes, rss_mb)):
        if target_mb >= 45:
            assert measured_mb >= 40.0, f"Case {idx+1} ({target_mb}MB) RSS too low: {measured_mb:.2f}MB"
        else:
            assert measured_mb < 35.0, f"Case {idx+1} ({target_mb}MB) RSS stacked: {measured_mb:.2f}MB"
