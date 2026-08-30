import pytest
from pathlib import Path
from util.template_manager import TemplateManager

class MockConfig:
    def __init__(self, data=None):
        self.data = data or {}

def test_builtin_templates_single_file(tmp_path):
    # Test Python
    py_target = tmp_path / "app.py"
    res = TemplateManager.generate(str(py_target))
    assert res is True
    assert py_target.exists()
    content = py_target.read_text()
    assert "def main():" in content
    assert 'if __name__ == "__main__":' in content

    # Test Java with class name substitution
    java_target = tmp_path / "Solution.java"
    res = TemplateManager.generate(str(java_target))
    assert res is True
    assert java_target.exists()
    j_content = java_target.read_text()
    assert "public class Solution {" in j_content

    # Test C++
    cpp_target = tmp_path / "main.cpp"
    res = TemplateManager.generate(str(cpp_target))
    assert res is True
    assert cpp_target.exists()
    assert "#include <iostream>" in cpp_target.read_text()

def test_custom_template_inline(tmp_path):
    cfg = MockConfig({
        "templates": {
            "cp": {
                "content": """// CP Template for {{name}}
#include <bits/stdc++.h>
using namespace std;
int main() { return 0; }
"""
            }
        }
    })

    target = tmp_path / "task_a.cpp"
    res = TemplateManager.generate(str(target), template_name="cp", config=cfg)
    assert res is True
    assert target.exists()
    text = target.read_text()
    assert "// CP Template for task_a" in text
    assert "#include <bits/stdc++.h>" in text

def test_multi_file_template(tmp_path):
    cfg = MockConfig({
        "templates": {
            "leetcode_rs": {
                "description": "LeetCode Rust starter",
                "files": [
                    {
                        "name": "main.rs",
                        "content": "mod solve;\nuse solve::Solution;\nfn main() { let s = Solution; }\n"
                    },
                    {
                        "name": "solve.rs",
                        "content": "pub struct Solution;\n"
                    }
                ]
            }
        }
    })

    target_dir = tmp_path / "problem_01"
    res = TemplateManager.generate(str(target_dir), template_name="leetcode_rs", config=cfg)
    assert res is True
    assert (target_dir / "main.rs").exists()
    assert (target_dir / "solve.rs").exists()
    assert "mod solve;" in (target_dir / "main.rs").read_text()
    assert "pub struct Solution;" in (target_dir / "solve.rs").read_text()

def test_overwrite_safety(tmp_path):
    target = tmp_path / "existing.py"
    target.write_text("ORIGINAL CONTENT")

    # Without force -> fail
    res = TemplateManager.generate(str(target), force=False)
    assert res is False
    assert target.read_text() == "ORIGINAL CONTENT"

    # With force -> succeed
    res = TemplateManager.generate(str(target), force=True)
    assert res is True
    assert "def main():" in target.read_text()

def test_multi_file_template_path_traversal_prevention(tmp_path):
    from util.errors import ConfigError

    cfg = MockConfig({
        "templates": {
            "malicious": {
                "files": [
                    {
                        "name": "../escape.txt",
                        "content": "pwned"
                    }
                ]
            }
        }
    })

    dest = tmp_path / "sandbox_dir"
    dest.mkdir()
    with pytest.raises(ConfigError, match="Path traversal detected"):
        TemplateManager.generate(str(dest), template_name="malicious", config=cfg, force=True)

    assert not (tmp_path / "escape.txt").exists()

def test_file_loader_path_traversal_prevention(tmp_path):
    from util.errors import ConfigError

    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("super_secret")

    sub_dir = tmp_path / "project"
    sub_dir.mkdir()

    with pytest.raises(ConfigError, match="outside base directory"):
        TemplateManager._load_template_content({"file": "../secret.txt"}, base_dir=sub_dir)

