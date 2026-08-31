import os
import re
import time
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
from util.output import Printer, Colors

class TestcasesRunner:
    """Batch testcases runner matching input/output files and reporting test results."""

    @staticmethod
    def _natural_sort_key(file_path: Path) -> List[Any]:
        """
        Produce sort key for natural numerical sorting of file paths.

        Args:
            file_path (Path): Path to file.

        Returns:
            List[Any]: Split token list supporting numeric order.
        """
        return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', file_path.name)]

    @classmethod
    def discover_test_pairs(cls, test_dir: Path) -> List[Tuple[Path, Path]]:
        """
        Discover input and output file pairs in a test directory.

        Args:
            test_dir (Path): Directory containing test files.

        Returns:
            List[Tuple[Path, Path]]: Sorted list of (input_path, expected_output_path) pairs.
        """
        if not test_dir.is_dir():
            return []

        all_files = sorted(list(test_dir.iterdir()), key=cls._natural_sort_key)
        in_files = [
            f for f in all_files 
            if f.is_file() and (f.suffix in ('.in', '.input') or f.name.startswith(('in', 'input')) and f.suffix == '.txt')
        ]

        pairs: List[Tuple[Path, Path]] = []
        
        for in_file in in_files:
            stem = in_file.stem
            # Clean stem from prefixes like 'input', 'in', etc.
            normalized = re.sub(r'^(?:input|in)[_-]?', '', stem, flags=re.IGNORECASE)
            
            # Look for matching output file
            expected_names = [
                f"{stem}.out", f"{stem}.ans", f"{stem}.output",
                f"out{normalized}.txt", f"output{normalized}.txt", f"ans{normalized}.txt",
                f"out_{normalized}.txt", f"output_{normalized}.txt", f"ans_{normalized}.txt",
                f"{normalized}.out", f"{normalized}.ans", f"{normalized}.output"
            ]
            
            out_file = None
            for name in expected_names:
                candidate = test_dir / name
                if candidate.exists() and candidate.is_file():
                    out_file = candidate
                    break
            
            if out_file:
                pairs.append((in_file, out_file))

        return sorted(pairs, key=lambda p: cls._natural_sort_key(p[0]))

    @classmethod
    def run_tests(cls, runner: Any, test_dir: Path, target_file: Path) -> bool:
        """
        Execute testsuite across all discovered testcase pairs.

        Args:
            runner (Any): CompilerRunner or BaseRunner instance.
            test_dir (Path): Directory containing test cases.
            target_file (Path): Target source file to compile and test.

        Returns:
            bool: True if all testcases passed, False otherwise.
        """
        pairs = cls.discover_test_pairs(test_dir)
        if not pairs:
            Printer.warning(f"No matching test pairs found in '{test_dir}'. Expected *.in + *.out or in*.txt + out*.txt.")
            return False

        Printer.info(f"Discovered {len(pairs)} test case(s) in {test_dir}")
        print(f"\n{Colors.BOLD}{Colors.CYAN}=== Running Test Suite ({len(pairs)} cases) ==={Colors.RESET}\n")

        passed_count = 0
        failed_count = 0

        # Phase 1: Compile once if target is a compiled language
        ext = target_file.suffix.lower()
        compiled_exts = {".c", ".cpp", ".cc", ".cxx", ".rs", ".java"}
        is_compiled = ext in compiled_exts
        bin_path = None

        if is_compiled and hasattr(runner, "get_executable_path"):
            bin_path = runner.get_executable_path(target_file)
            runner.flags["build_only"] = True
            try:
                compile_ok = runner._handle_single_file(target_file)
                if not compile_ok:
                    Printer.error(f"Compilation failed for {target_file}")
                    return False
            finally:
                runner.flags["build_only"] = False

        orig_stdin = runner.flags.get("stdin")
        orig_expect = runner.flags.get("expect")
        orig_buffered_stdin = getattr(runner, "_buffered_stdin", None)

        try:
            # Phase 2: Execute all test cases
            for idx, (in_path, out_path) in enumerate(pairs, start=1):
                with open(in_path, "r", encoding="utf-8", errors="ignore") as f:
                    in_content = f.read()

                with open(out_path, "r", encoding="utf-8", errors="ignore") as f:
                    expected_content = f.read()

                # Set test inputs into runner
                runner.flags["stdin"] = str(in_path)
                runner.flags["expect"] = str(out_path)
                runner._buffered_stdin = in_content

                start_t = time.perf_counter()
                try:
                    if is_compiled and bin_path and bin_path.exists() and hasattr(runner, "_execute_binary"):
                        success = runner._execute_binary(bin_path)
                    else:
                        success = runner._handle_single_file(target_file)
                    elapsed = time.perf_counter() - start_t
                    
                    if success:
                        passed_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    failed_count += 1
                    Printer.error(f"Test #{idx} ({in_path.name}) failed with exception: {e}")
        finally:
            runner.flags["stdin"] = orig_stdin
            runner.flags["expect"] = orig_expect
            runner._buffered_stdin = orig_buffered_stdin

        total = len(pairs)
        print(f"\n{Colors.BOLD}{Colors.CYAN}=== Test Summary ==={Colors.RESET}")
        summary_color = Colors.GREEN if passed_count == total else Colors.RED
        print(f"{summary_color}Passed: {passed_count}/{total} ({(passed_count/total)*100:.1f}%){Colors.RESET}\n")
        return passed_count == total
