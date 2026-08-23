import sys
from pathlib import Path
from typing import Set, List
from util.output import Printer, Colors

class ConfigInitializer:
    """
    Scaffolds and generates a tailored Run.toml configuration based on project inspection.
    """

    @classmethod
    def detect_technologies(cls, root: Path) -> Set[str]:
        """
        Inspect directory structure and file extensions to detect active technologies.

        Args:
            root (Path): Root directory to inspect.

        Returns:
            Set[str]: Set of detected technology identifiers.
        """
        detected: Set[str] = set()
        ignore_dirs = {'.git', '.venv', 'venv', 'env', 'node_modules', '.run_cache', 'build', 'target'}

        for p in root.rglob("*"):
            if any(part in ignore_dirs for part in p.parts):
                continue
            if p.is_file():
                match p.name:
                    case "Cargo.toml":
                        detected.add("rust_cargo")
                    case "CMakeLists.txt":
                        detected.add("cmake")
                    case "go.mod":
                        detected.add("go")
                    case "build.zig":
                        detected.add("zig")
                    case "package.json":
                        detected.add("node")
                    case "Makefile":
                        detected.add("make")

                match p.suffix.lower():
                    case ".cpp" | ".cc" | ".cxx" | ".hpp":
                        detected.add("cpp")
                    case ".c" | ".h":
                        detected.add("c")
                    case ".py":
                        detected.add("python")
                    case ".rs":
                        detected.add("rust")
                    case ".java":
                        detected.add("java")
                    case ".go":
                        detected.add("go")
                    case ".zig":
                        detected.add("zig")

        return detected

    @classmethod
    def generate_config_content(cls, technologies: Set[str]) -> str:
        """
        Generate customized Run.toml content based on detected technologies.

        Args:
            technologies (Set[str]): Detected technology set.

        Returns:
            str: Generated TOML configuration string.
        """
        lines: List[str] = [
            "# ==============================================================================",
            "# Auto-Generated Run.toml Configuration",
            "# ==============================================================================",
            "",
            "[core]",
            "exclude_files = []",
            "exclude_extensions = [\".md\", \".txt\", \".json\", \".yaml\", \".bak\"]",
            ""
        ]

        # Tasks section
        lines.append("[tasks]")
        if "python" in technologies:
            lines.append("test = \"pytest tests/ -v\"")
        elif "rust" in technologies or "rust_cargo" in technologies:
            lines.append("test = \"cargo test\"")
            lines.append("build = \"cargo build --release\"")
        elif "cmake" in technologies:
            lines.append("build = \"cmake -B build && cmake --build build\"")
            lines.append("clean = \"rm -rf build\"")
        else:
            lines.append("# test = \"pytest tests/\"")
            lines.append("# build = \"gcc -O3 main.c -o app\"")
        lines.append("")

        # Projects section
        if "cmake" in technologies:
            lines.append("[projects.cmake]")
            lines.append("file = \"CMakeLists.txt\"")
            lines.append("build = \"cmake -B build && cmake --build build\"")
            lines.append("run = \"./build/app\"")
            lines.append("")
        elif "node" in technologies:
            lines.append("[projects.node]")
            lines.append("file = \"package.json\"")
            lines.append("command = \"npm start\"")
            lines.append("")

        # Presets section
        lines.append("[presets.debug]")
        if "cpp" in technologies:
            lines.append("cpp = [\"-g\", \"-Wall\", \"-Wextra\", \"-std=c++20\", \"-O0\"]")
        if "c" in technologies:
            lines.append("c = [\"-g\", \"-Wall\", \"-Wextra\", \"-O0\"]")
        if "rust" in technologies:
            lines.append("rust = [\"-g\"]")
        if "java" in technologies:
            lines.append("java = [\"-g\"]")
        if not ("cpp" in technologies or "c" in technologies or "rust" in technologies or "java" in technologies):
            lines.append("# c = [\"-g\", \"-Wall\", \"-O0\"]")
            lines.append("# cpp = [\"-g\", \"-Wall\", \"-std=c++20\", \"-O0\"]")
        lines.append("")

        lines.append("[presets.release]")
        if "cpp" in technologies:
            lines.append("cpp = [\"-O3\", \"-std=c++20\", \"-march=native\", \"-DNDEBUG\"]")
        if "c" in technologies:
            lines.append("c = [\"-O3\", \"-march=native\", \"-DNDEBUG\"]")
        if "rust" in technologies:
            lines.append("rust = [\"-C\", \"opt-level=3\"]")
        if "java" in technologies:
            lines.append("java = [\"-O\"]")
        if not ("cpp" in technologies or "c" in technologies or "rust" in technologies or "java" in technologies):
            lines.append("# c = [\"-O3\", \"-DNDEBUG\"]")
            lines.append("# cpp = [\"-O3\", \"-std=c++20\", \"-DNDEBUG\"]")
        lines.append("")

        return "\n".join(lines)

    @classmethod
    def init_config(cls, target_dir: Path = Path("."), force: bool = False) -> bool:
        """
        Create a new Run.toml in the target directory.

        Args:
            target_dir (Path): Destination directory.
            force (bool): Skip overwrite confirmation.

        Returns:
            bool: True if created successfully.
        """
        dest = target_dir / "Run.toml"
        if dest.exists() and not force:
            Printer.warning(f"{dest} already exists.")
            ans = input("Overwrite existing Run.toml? [y/N]: ").strip().lower()
            if ans not in ("y", "yes"):
                Printer.info("Initialization cancelled.")
                return False

        techs = cls.detect_technologies(target_dir)
        content = cls.generate_config_content(techs)

        try:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(content)
            detected_str = ", ".join(sorted(techs)) if techs else "generic"
            Printer.action("INIT", f"Generated Run.toml for detected environment: [{detected_str}]", Colors.GREEN)
            return True
        except Exception as e:
            Printer.error(f"Failed to write Run.toml: {e}")
            return False
