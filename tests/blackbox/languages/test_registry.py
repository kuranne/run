"""Black-box CLI HandlerRegistry precedence (custom [[languages]] overrides built-ins)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, strip_ansi


def test_custom_language_overrides_builtin_extension(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[[languages]]\n"
        'name = "custom_py"\n'
        'extensions = [".py"]\n'
        'command = "python3 -c \\"print(\'OVERRIDDEN_PY\')\\""\n',
        encoding="utf-8",
    )
    (work_dir / "orig.py").write_text('print("ORIGINAL_PY")\n', encoding="utf-8")

    res = run_cli(["orig.py"])
    assert res.returncode == 0, combined_output(res)
    assert "OVERRIDDEN_PY" in res.stdout
    assert "ORIGINAL_PY" not in res.stdout


def test_registry_rejects_unsupported_extension(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "note.xyz").write_text("plain\n", encoding="utf-8")
    res = run_cli(["note.xyz"])
    assert res.returncode == 1
    assert "Unsupported extension: .xyz" in strip_ansi(combined_output(res))
