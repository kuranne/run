"""Black-box CLI template variable and ${env:VAR} substitution tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_substitutions_file_and_env_in_custom_language(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[[languages]]\n"
        'name = "sublang"\n'
        'extensions = [".sub"]\n'
        'command = "python3 -c \\"import sys; print(\'SUB:\' + \'|\'.join(sys.argv[1:]))\\" {stem} {ext} ${env:MY_SAFE_VAR} ${env:LD_PRELOAD}"\n',
        encoding="utf-8",
    )
    (work_dir / "demo.sub").write_text("payload\n", encoding="utf-8")

    res = run_cli(
        ["demo.sub"],
        env_overrides={"MY_SAFE_VAR": "hello_safe", "LD_PRELOAD": "evil.so"},
    )
    assert res.returncode == 0, combined_output(res)
    out = strip_ansi(res.stdout)
    assert "SUB:demo|.sub|hello_safe" in out
    assert "evil.so" not in out
    assert "[ WARN ]" in out
