"""Black-box CLI adversarial edge cases: deep -L nesting, spaces in paths, and large stdin/stdout."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output


def test_adversarial_deep_autolink_and_spaced_paths(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    deep = work_dir / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (work_dir / "main.c").write_text(
        '#include <stdio.h>\nint deep_fn(void);\nint main(void) { printf("DEEP=%d\\n", deep_fn()); return 0; }\n',
        encoding="utf-8",
    )
    (deep / "leaf.c").write_text("int deep_fn(void) { return 99; }\n", encoding="utf-8")

    res = run_cli(["-L"])
    assert res.returncode == 0, combined_output(res)
    assert "DEEP=99" in res.stdout

    spaced = work_dir / "folder with space"
    spaced.mkdir()
    (spaced / "my script.py").write_text(
        'import sys\nprint("PIPE_LEN=" + str(len(sys.stdin.read())))\n',
        encoding="utf-8",
    )
    payload = "x" * 65536
    res_pipe = run_cli(["-i", "-", str(spaced / "my script.py")], stdin_text=payload)
    assert res_pipe.returncode == 0, combined_output(res_pipe)
    assert "PIPE_LEN=65536" in res_pipe.stdout
