"""Black-box CLI Java single/multi-file dry-run, hyphenated class rejection, and execution tests."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from conftest import combined_output, require_optional_tool, strip_ansi


def test_java_dry_run_and_hyphenated_main_rejected(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    (work_dir / "Hello.java").write_text(
        'public class Hello { public static void main(String[] args) { System.out.println("HI"); } }\n',
        encoding="utf-8",
    )
    res = run_cli(["--dry-run", "Hello.java"])
    assert res.returncode == 0, combined_output(res)
    out = strip_ansi(res.stdout)
    assert "javac" in out
    assert "java -cp" in out

    (work_dir / "-Bad.java").write_text(
        'class Bad { public static void main(String[] args) {} }\n',
        encoding="utf-8",
    )
    bad_res = run_cli(["--dry-run", "./-Bad.java"])
    assert bad_res.returncode == 1
    assert "Invalid main class name: -Bad" in strip_ansi(combined_output(bad_res))


def test_java_execution_when_jdk_installed(
    work_dir: Path, run_cli: Callable[..., object]
) -> None:
    require_optional_tool("javac")
    require_optional_tool("java")
    (work_dir / "Hello.java").write_text(
        'public class Hello { public static void main(String[] args) { System.out.println("JAVA_OK"); } }\n',
        encoding="utf-8",
    )
    res = run_cli(["Hello.java"])
    assert res.returncode == 0, combined_output(res)
    assert "JAVA_OK" in res.stdout
