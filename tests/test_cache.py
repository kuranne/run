"""Black-box CLI XDG cache (~/.cache/run), --no-cache, and --clean-cache tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from conftest import combined_output, strip_ansi


def test_cache_shebang_hit_invalidation_and_clean(
    work_dir: Path, isolated_env: dict[str, Any], run_cli: Callable[..., object]
) -> None:
    script = work_dir / "cached_tool.c"
    script.write_text(
        "#!/usr/bin/env run\n"
        '#include <stdio.h>\n'
        'int main(void) { printf("CACHE_V1\\n"); return 0; }\n',
        encoding="utf-8",
    )

    r1 = run_cli(["cached_tool.c"])
    assert r1.returncode == 0, combined_output(r1)
    assert "CACHE_V1" in r1.stdout
    cache_root = isolated_env["xdg_cache"] / "run"
    assert cache_root.is_dir()

    r2 = run_cli(["cached_tool.c"])
    assert r2.returncode == 0, combined_output(r2)
    assert "[ INFO ] Using cached binary:" in strip_ansi(r2.stdout)
    assert "CACHE_V1" in r2.stdout

    r_nocache = run_cli(["--no-cache", "cached_tool.c"])
    assert r_nocache.returncode == 0, combined_output(r_nocache)
    assert "Using cached binary:" not in strip_ansi(r_nocache.stdout)

    r_clean = run_cli(["--clean-cache"])
    assert r_clean.returncode == 0, combined_output(r_clean)
    assert "[ CLEAN ] Build cache cleared successfully." in strip_ansi(r_clean.stdout)
