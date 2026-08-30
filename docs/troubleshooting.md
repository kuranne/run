# Troubleshooting & Performance Tips

Common issues, solutions, and performance optimizations when using `run`.


## Diagnostics with `run --doctor`

If you experience compilation or runtime issues, run the diagnostic scanner to verify your local toolchain:

```bash
run --doctor
```

This scans for available compilers (GCC, Clang, rustc, javac, zig, go), interpreters (Python, Node, Ruby, Perl, Lua), build tools (Make, CMake, Ninja), and active virtual environments.


## Common Issues & Solutions

### Issue: "Interpreter or compiler not found"
**Cause**: The required binary is not installed or not in your system `PATH`.
**Solution**:
1. Check `run --doctor` to identify missing tools.
2. Override the binary in `Run.toml`:
   ```toml
   [runners]
   python = "/usr/local/bin/python3.11"
   ```


### Issue: "Cached build not updating"
**Cause**: Build caching may not detect header-only changes outside the monitored dependency tree.
**Solution**:
1. Bypass cache for the current run:
   ```bash
   run file.cpp --no-cache
   ```
2. Or clear the local cache:
   ```bash
   run --clean
   ```


### Issue: "Command failed or unexpected output"
**Solution**:
1. Use `--dry-run` (`-d`) to preview the exact generated command:
   ```bash
   run main.cpp -d
   ```
2. Enable debug logging with `-v` or full stack traces with `-vv`:
   ```bash
   run main.cpp -v
   ```
3. Use `--keep` to preserve the output binary for manual inspection:
   ```bash
   run main.cpp --keep
   ```


## Performance Optimization Tips

1. **Leverage Build Presets**:
   Avoid typing long compiler flags by configuring `[presets]` in `Run.toml` (e.g. `run main.cpp -p release`).
2. **Parallel Compilation (`-j <N>`)**:
   When compiling multi-file C/C++ projects, specify parallel worker threads:
   ```bash
   run *.cpp -m -j 8
   ```
3. **Use Watch Mode (`-w`) During Iterative Development**:
   Keeps the runner active and re-compiles instantly upon file save:
   ```bash
   run solution.cpp -w -t
   ```
