# Code Templates & Scaffolding Guide

`run` allows you to quickly generate single-file starter source code or multi-file project scaffolding using built-in templates or custom templates defined in `Run.toml`.

## Single-File Scaffolding (`run --new`)

Generate a new starter file based on its extension:

```bash
run --new solution.cpp
run --new script.py
run --new Solution.java
run --new main.rs
```

## Dynamic Placeholders

Templates support automatic placeholder substitution:
- `{{name}}`: File stem (e.g. `Solution` for `Solution.java`)
- `{{filename}}`: Full file name (e.g. `Solution.java`)
- `{{date}}`: Current date (`YYYY-MM-DD`)
- `{{year}}`: Current year (`YYYY`)

## Custom Templates in `Run.toml`

### Inline Template Definition
```toml
[templates.cp]
extension = ".cpp"
content = """// Problem: {{name}}
// Date: {{date}}
#include <bits/stdc++.h>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(NULL);
    return 0;
}
"""
```

Usage:
```bash
run --new task_a.cpp --template cp
```

### External File Template Definition
```toml
[templates.fast_io]
extension = ".cpp"
file = "./templates/fast_io.cpp"
```

##. Multi-File Project Bundles

For competitive programming platforms (like LeetCode) or modules requiring multiple files (e.g. `main.rs` + `solve.rs`, or `main.cpp` + `solution.hpp`), configure a `files` list in `Run.toml`:

```toml
[templates.leetcode_rs]
description = "LeetCode Rust starter with Solution struct"
files = [
    { name = "main.rs", content = """mod solve;
use solve::Solution;

fn main() {
    let sol = Solution::new();
    println!("{:?}", sol.solve());
}""" },
    { name = "solve.rs", content = """pub struct Solution;

impl Solution {
    pub fn new() -> Self {
        Solution
    }
    
    pub fn solve(&self) -> i32 {
        0
    }
}""" }
]
```

Usage:
```bash
run --new ./problem1 --template leetcode_rs
```
*Creates `problem1/main.rs` and `problem1/solve.rs` in one command.*

## Overwrite Protection

If a target file already exists, `run --new` will refuse to overwrite it to prevent accidental data loss:
```bash
run --new solution.cpp
# [ ERROR ] File 'solution.cpp' already exists. Use -f / --force to overwrite.
```

To force overwrite:
```bash
run --new solution.cpp -f
```
