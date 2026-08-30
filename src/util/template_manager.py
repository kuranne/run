from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
from util.output import Printer, Colors
from util.errors import ConfigError

class TemplateManager:
    """
    Manager for generating starter code templates and multi-file project scaffolding.
    """

    BUILTIN_TEMPLATES: Dict[str, str] = {
        ".c": """#include <stdio.h>

int main(void) {
    printf("Hello, World!\\n");
    return 0;
}
""",
        ".cpp": """#include <iostream>

using namespace std;

int main() {
    cout << "Hello, World!" << endl;
    return 0;
}
""",
        ".cc": """#include <iostream>

using namespace std;

int main() {
    cout << "Hello, World!" << endl;
    return 0;
}
""",
        ".py": """def main():
    print("Hello, World!")

if __name__ == "__main__":
    main()
""",
        ".rs": """fn main() {
    println!("Hello, World!");
}
""",
        ".java": """public class {{name}} {
    public static void main(String[] args) {
        System.out.println("Hello, World!");
    }
}
""",
        ".go": """package main

import "fmt"

func main() {
    fmt.Println("Hello, World!")
}
""",
        ".zig": """const std = @import("std");

pub fn main() !void {
    const stdout = std.io.getStdOut().writer();
    try stdout.print("Hello, World!\\n", .{});
}
""",
        ".js": """console.log("Hello, World!");
""",
        ".ts": """console.log("Hello, World!");
""",
        ".sh": """#!/usr/bin/env bash
set -euo pipefail

echo "Hello, World!"
"""
    }

    @classmethod
    def _interpolate(cls, content: str, target_path: Path) -> str:
        """
        Interpolate dynamic placeholders within template content.

        Args:
            content (str): Template string.
            target_path (Path): Path of the destination file.

        Returns:
            str: Interpolated content.
        """
        now = datetime.now()
        replacements = {
            "{{name}}": target_path.stem,
            "{{filename}}": target_path.name,
            "{{date}}": now.strftime("%Y-%m-%d"),
            "{{year}}": now.strftime("%Y"),
        }
        for key, val in replacements.items():
            content = content.replace(key, val)
        return content

    @classmethod
    def _load_template_content(cls, template_def: Any, base_dir: Path) -> Optional[str]:
        """
        Load template content from string or file path.

        Args:
            template_def (Any): String or dictionary definition.
            base_dir (Path): Base directory for resolving relative file paths.

        Returns:
            Optional[str]: Loaded template string.
        """
        if isinstance(template_def, str):
            return template_def
        if isinstance(template_def, dict):
            if "content" in template_def:
                return str(template_def["content"])
            if "file" in template_def:
                file_path = base_dir / template_def["file"]
                if file_path.exists():
                    return file_path.read_text(encoding="utf-8")
                raise ConfigError(f"Template file not found: {file_path}")
        return None

    @classmethod
    def generate(
        cls,
        target_name: str,
        template_name: Optional[str] = None,
        config: Optional[Any] = None,
        force: bool = False
    ) -> bool:
        """
        Generate single-file or multi-file template scaffolding.

        Args:
            target_name (str): Destination file or directory name.
            template_name (Optional[str]): Template identifier from Run.toml.
            config (Optional[Any]): Config instance containing custom templates.
            force (bool): Whether to overwrite existing files.

        Returns:
            bool: True if generation succeeded, False otherwise.
        """
        target = Path(target_name)
        templates_cfg = config.data.get("templates", {}) if config and hasattr(config, "data") else {}

        # 1. Resolve template definition
        selected_def = None
        if template_name:
            if template_name in templates_cfg:
                selected_def = templates_cfg[template_name]
            else:
                # Check builtins with leading dot or clean name
                ext = f".{template_name}" if not template_name.startswith(".") else template_name
                if ext in cls.BUILTIN_TEMPLATES:
                    selected_def = cls.BUILTIN_TEMPLATES[ext]
                else:
                    Printer.error(f"Template '{template_name}' not found in Run.toml or built-in templates.")
                    return False
        else:
            # Infer from target extension or name
            ext = target.suffix.lower()
            if ext and ext[1:] in templates_cfg:
                selected_def = templates_cfg[ext[1:]]
            elif ext and ext in cls.BUILTIN_TEMPLATES:
                selected_def = cls.BUILTIN_TEMPLATES[ext]
            elif target.name in templates_cfg:
                selected_def = templates_cfg[target.name]
            else:
                Printer.error(f"Cannot infer template for '{target_name}'. Specify --template <name>.")
                return False

        # 2. Check if multi-file template
        if isinstance(selected_def, dict) and "files" in selected_def:
            files_list = selected_def["files"]
            if not isinstance(files_list, list):
                Printer.error(f"Invalid 'files' definition in template '{template_name or target_name}'. Expected a list.")
                return False

            dest_dir = target if (not target.suffix or target.is_dir()) else target.parent
            dest_dir.mkdir(parents=True, exist_ok=True)

            # Check all files for overwrite safety before writing
            if not force:
                for file_entry in files_list:
                    fname = file_entry.get("name")
                    if fname and (dest_dir / fname).exists():
                        Printer.error(f"File '{(dest_dir / fname)}' already exists. Use -f / --force to overwrite.")
                        return False

            # Create files
            for file_entry in files_list:
                fname = file_entry.get("name")
                if not fname:
                    continue
                file_dest = dest_dir / fname
                raw_content = cls._load_template_content(file_entry, Path.cwd()) or ""
                interpolated = cls._interpolate(raw_content, file_dest)
                file_dest.parent.mkdir(parents=True, exist_ok=True)
                file_dest.write_text(interpolated, encoding="utf-8")
                Printer.action("CREATE", f"Generated {file_dest}", Colors.GREEN)

            return True

        # 3. Single-file template
        raw_content = cls._load_template_content(selected_def, Path.cwd())
        if raw_content is None:
            Printer.error(f"Could not load template content for '{template_name or target_name}'.")
            return False

        if target.exists() and not force:
            Printer.error(f"File '{target}' already exists. Use -f / --force to overwrite.")
            return False

        target.parent.mkdir(parents=True, exist_ok=True)
        interpolated = cls._interpolate(raw_content, target)
        target.write_text(interpolated, encoding="utf-8")
        Printer.action("CREATE", f"Generated {target}", Colors.GREEN)
        return True
