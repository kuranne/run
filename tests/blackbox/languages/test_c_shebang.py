"""Black-box CLI C/C++ shebang script execution, inline flags, #line 2, and caching tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_c_shebang_inline_flags_and_trailing_args(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    script = work_dir / "shebang_cli.c"
    script.write_text(
        "#!/usr/bin/env run\n"
        "// run: -DGREETING=42\n"
        "#include <stdio.h>\n"
        "int main(int argc, char **argv) {\n"
        '    printf("G=%d ARG=%s\\n", GREETING, argc > 1 ? argv[1] : "none");\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )

    r1 = run_cli(["shebang_cli.c", "hello_arg"])
    assert r1.returncode == 0, combined_output(r1)
    assert "G=42 ARG=hello_arg" in r1.stdout

    r2 = run_cli(["shebang_cli.c", "second_arg"])
    assert r2.returncode == 0, combined_output(r2)
    assert "[ INFO ] Using cached binary:" in strip_ansi(r2.stdout)
    assert "G=42 ARG=second_arg" in r2.stdout


def test_c_shebang_preserves_line_numbers(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "bad_line.c").write_text(
        "#!/usr/bin/env run\nint broken = ;\n",
        encoding="utf-8",
    )
    res = run_cli(["bad_line.c"])
    assert res.returncode == 1
    assert "bad_line.c" in res.stderr
    assert ":2:" in res.stderr
