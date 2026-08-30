# Testing & Benchmarking Guide

`run` includes built-in competitive programming tools, execution benchmarks, output expectation verification, and batch testcase runners.

---

## Benchmarking: Execution Time & Memory

### Measuring Time (`-t, --time`)
Measures exact wall-clock execution time:
```bash
run solution.cpp -t
# [ TIME ] Took 0.042s
```

### Measuring Peak Memory (`-M, --mem, --memory`)
Tracks peak Resident Set Size (RSS) during execution:
```bash
run solution.py -M
# [ MEMORY ] Peak Memory: 18.42 MB
```

### Combined Benchmark (`-tM`)
```bash
run solution.cpp -tM
# [ BENCH ] Took 0.038s | Peak Memory: 4.12 MB
```

---

## Standard Input Redirection (`-i, --stdin`)

### Pipe from Shell Stream
```bash
echo "10 20" | run solution.py -i
```

### Read from File
```bash
run solution.cpp -tM -i input.txt
```

---

## Output Expectation Verification (`--expect`)

Automatically compares program output against an expected answer file and prints a colored unified diff if they differ:

```bash
run solution.cpp -i in.txt --expect out.txt -tM
```

- If matches: `[ PASS ] Output matches out.txt`
- If differs: `[ FAIL ] Output mismatch with out.txt` followed by line-by-line diff.

---

## Batch Testcase Directory Runner (`--test-dir`)

Run a solution against an entire suite of testcases in one command:

```bash
run solution.cpp --test-dir ./testcases/ -tM
```

`run` automatically pairs matching files in the directory:
- `01.in` + `01.out`
- `test_01.in` + `test_01.ans`
- `in1.txt` + `out1.txt`

### Sample Output:
```text
=== Running Test Suite (3 cases) ===
[ 1/3 ] PASS (01.in -> 01.out) [0.012s]
[ 2/3 ] PASS (02.in -> 02.out) [0.015s]
[ 3/3 ] FAIL (03.in -> 03.out) [0.014s]

=== Test Summary ===
Passed: 2/3 (66.7%)
```
