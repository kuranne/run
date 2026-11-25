"""Black-box CLI Run.toml config loading, XDG fallback, presets, and dropped alias checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from conftest import combined_output, strip_ansi


def test_config_global_xdg_fallback_and_presets(
    work_dir: Path, isolated_env: dict[str, Any], run_cli: Callable[..., object]
) -> None:
    global_dir = isolated_env["xdg_config"] / "run"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "Run.toml").write_text(
        "[presets.release_fast]\n"
        'c = ["-DGLOBAL_VAL=77"]\n',
        encoding="utf-8",
    )
    (work_dir / "main.c").write_text(
        '#include <stdio.h>\nint main(void) { printf("GV=%d\\n", GLOBAL_VAL); return 0; }\n',
        encoding="utf-8",
    )

    res = run_cli(["-p", "release_fast", "main.c"])
    assert res.returncode == 0, combined_output(res)
    assert "GV=77" in res.stdout


@pytest.mark.parametrize(
    "deprecated_section",
    [
        "[runner]\nc = \"gcc\"\n",
        "[preset.fast]\nc = [\"-O2\"]\n",
        "[[language]]\nname = \"x\"\nextensions = [\".x\"]\ncommand = \"echo {file}\"\n",
        "[exclude]\nfiles = [\"skip.c\"]\n",
    ],
)
def test_config_rejects_deprecated_aliases(
    work_dir: Path,
    run_cli: Callable[..., object],
    deprecated_section: str,
) -> None:
    (work_dir / "Run.toml").write_text(deprecated_section, encoding="utf-8")
    (work_dir / "a.py").write_text("print(1)\n", encoding="utf-8")

    res = run_cli(["a.py"])
    assert res.returncode == 1
    assert "[ ERROR ]" in strip_ansi(combined_output(res))
