"""Black-box CLI [[languages]] custom compiler, command, and legacy runner tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output


def test_custom_language_command_and_compile_modes(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "Run.toml").write_text(
        "[[languages]]\n"
        'name = "mycmd"\n'
        'extensions = [".mycmd"]\n'
        'command = "python3 -c \\"import sys; print(\'CMD:\' + sys.argv[1])\\" {file}"\n'
        "\n"
        "[[languages]]\n"
        'name = "mycc"\n'
        'extensions = [".mycc"]\n'
        'compile = "gcc -x c {file} -o {out}"\n',
        encoding="utf-8",
    )
    (work_dir / "hello.mycmd").write_text("hi\n", encoding="utf-8")
    (work_dir / "hello.mycc").write_text(
        '#include <stdio.h>\nint main(void) { printf("MYCC_OK\\n"); return 0; }\n',
        encoding="utf-8",
    )

    r_cmd = run_cli(["hello.mycmd"])
    assert r_cmd.returncode == 0, combined_output(r_cmd)
    assert "CMD:hello.mycmd" in r_cmd.stdout

    r_cc = run_cli(["hello.mycc"])
    assert r_cc.returncode == 0, combined_output(r_cc)
    assert "MYCC_OK" in r_cc.stdout
