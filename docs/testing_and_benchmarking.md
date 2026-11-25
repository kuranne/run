# Testing, Benchmarking & Stress Testing Guide

`run` includes built-in execution metrics, flexible stdin handling, output expectation diffs, batch testcase runners, micro-benchmarking, and differential stress testing.

---

## 1. Execution Time & Peak Memory (`-t`, `-M`)

```bash
run solution.cpp -t         # Wall-clock duration
run solution.py -M          # Peak Resident Set Size (RSS)
run solution.cpp -tM        # Both time and peak memory
run -t -M ./prebuilt_bin    # Works on local binaries
run -t -M -- ls -la         # Works on any PATH command
```

---

## 2. Standard Input (`-i` / `--stdin`, `--stdin-file`, `--stdin-text`)

| Flag | Behavior |
|------|----------|
| `-i` / `--stdin` (no value or `-`) | Reads piped stdin from the shell (`echo "1 2" \| run app.py -i`) |
| `-i <source>` / `--stdin <source>` | **Dynamic**: reads from `<source>` if it is an existing file, otherwise passes `<source>` as inline text (unescaping `\n`, `\t`, `\r`, `\\`) |
| `--stdin-file <file>` | Strictly reads from `<file>` (errors if missing or a directory) |
| `--stdin-text <text>` | Strictly passes inline `<text>` (unescaping `\n`, `\t`, `\r`, `\\`) |

```bash
run main.c --stdin-text "5 10\n"
run main.c --stdin-file input.txt
run main.c -i "5 10\n"
run main.c -i input.txt
echo "5 10" | run main.c -i
```

---

## 3. Output Expectation Diffs (`--expect`)

Compare program `stdout` against an expected file with colorized unified diffs on mismatch:

```bash
run solution.cpp -i in.txt --expect out.txt -tM
```

---

## 4. Batch Testcase Runner (`--test-dir` & `--filter`)

Compiles compiled-language targets **once** and runs every paired testcase (`*.in`+`*.out`, `*.in`+`*.ans`, `in*.txt`+`out*.txt`) in natural numeric order:

```bash
run solution.cpp --test-dir cases/ -tM
run solution.cpp --test-dir cases/ --filter "edge_*"
```

---

## 5. Micro-Benchmarking (`--bench [N]` & `--warmup [W]`)

Compiles once (reusing the SHA-256 build cache), runs warmup + measured iterations, suppresses noisy child stdout (unless `-v` is passed), fails fast on errors, and reports **min / median / mean / p95 / max** wall-clock time and peak RSS (with a Unicode sparkline `▁▂▃▄▅▆▇█` on TTYs):

```bash
run --bench main.c                      # Default: 10 runs, 2 warmup
run --bench 50 --warmup 5 main.c        # Custom counts
run --bench 20 ./a.out                  # Prebuilt binary
run --bench 20 -- ls -la                # PATH command
```

---

## 6. Differential Stress Testing (`--stress`)

Compare a fast candidate (`sol`) against a trusted reference (`brute`) on randomly generated inputs (`gen`) until they diverge:

```bash
run sol.cpp --stress gen.py brute.cpp
run sol.rs  --stress gen.cpp brute.py --runs 500 --seed 42
run sol.cpp --stress gen.py brute.cpp --checker check.py
```

### Generator (`gen`) Contract
- Receives the current seed as `argv[1]` and `RUN_SEED=<seed>` in its environment.
- Prints **one complete test case** to `stdout`, which `run` pipes into both `brute` and `sol`.

### Custom Checker (`--checker`) Contract
- Receives `<in_path> <expected_path> <actual_path>` as positional arguments.
- Exits `0` if valid, non-zero if invalid.

### On Failure
- Stops immediately (fail-fast), saves `fail.in`, `expected.out`, and `actual.out` (in cwd or `--out-dir`), and prints the failing seed and colorized unified diff.
