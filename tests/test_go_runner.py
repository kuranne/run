import shutil
import pytest
from pathlib import Path
from util.config import Config
from runner.core import CompilerRunner

HAS_GO = shutil.which("go") is not None

def test_go_builtin_config():
    """Verify Go is configured out of the box."""
    cfg = Config()
    lang = cfg.get_language_by_extension(".go")
    assert lang is not None
    assert lang["name"] == "go"
    assert ".go" in lang["extensions"]
    assert "compile" in lang
    assert "{files}" in lang["compile"] or "${files}" in lang["compile"]


@pytest.mark.skipif(not HAS_GO, reason="Go toolchain not installed")
def test_go_single_file_execution(tmp_path, capfd):
    """Verify single-file Go program compilation and execution."""
    src = tmp_path / "hello.go"
    src.write_text("""package main

import "fmt"

func main() {
    fmt.Println("Hello from Single Go")
}
""")
    runner = CompilerRunner({"quiet": True, "no_color": True})
    try:
        runner.compile_and_run([str(src)], multi=False)
        captured = capfd.readouterr()
        assert "Hello from Single Go" in captured.out
    finally:
        runner.cleanup()


@pytest.mark.skipif(not HAS_GO, reason="Go toolchain not installed")
def test_go_multi_file_package_compilation(tmp_path, capfd):
    """
    Verify multi-file Go package compilation (-m) with cross-file references.
    Reproduces the user's multi-file scenario: addTwoNumber.go, listNode.go, parser.go, main.go.
    """
    f_listnode = tmp_path / "listNode.go"
    f_listnode.write_text("""package main

type ListNode struct {
    Val  int
    Next *ListNode
}
""")

    f_add = tmp_path / "addTwoNumber.go"
    f_add.write_text("""package main

func AddTwo(a, b int) int {
    return a + b
}
""")

    f_parser = tmp_path / "parser.go"
    f_parser.write_text("""package main

import "strconv"

func ParseInt(s string) int {
    v, _ := strconv.Atoi(s)
    return v
}
""")

    f_main = tmp_path / "main.go"
    f_main.write_text("""package main

import "fmt"

func main() {
    node := &ListNode{Val: 40, Next: nil}
    added := AddTwo(node.Val, ParseInt("2"))
    fmt.Printf("Result: %d\\n", added)
}
""")

    files = [str(f_add), str(f_listnode), str(f_parser), str(f_main)]
    runner = CompilerRunner({"quiet": True, "no_color": True})
    try:
        runner.compile_and_run(files, multi=True)
        captured = capfd.readouterr()
        assert "Result: 42" in captured.out
    finally:
        runner.cleanup()


@pytest.mark.skipif(not HAS_GO, reason="Go toolchain not installed")
def test_go_custom_template_override(tmp_path, capfd):
    """Verify that Run.toml can override Go compilation with interpreter mode or custom flags."""
    toml_path = tmp_path / "Run.toml"
    toml_path.write_text("""
[[languages]]
name = "go"
extensions = [".go"]
command = "go run {files}"
""")
    cfg = Config(tmp_path)
    src = tmp_path / "app.go"
    src.write_text("""package main

import "fmt"

func main() {
    fmt.Println("Custom Go Run Template")
}
""")
    runner = CompilerRunner({"quiet": True, "no_color": True})
    runner.config = cfg
    try:
        runner.compile_and_run([str(src)], multi=False)
        captured = capfd.readouterr()
        assert "Custom Go Run Template" in captured.out
    finally:
        runner.cleanup()
