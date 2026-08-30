# Sandboxed & Isolated Execution Guide

`run` provides multi-tiered execution isolation to safely compile and execute untrusted or experimental code. Depending on your security and environment requirements, you can choose between native OS restrictions and full containerized virtualization.

---

## 1. Native Process Restriction (`--restrict`)

The `--restrict` flag enforces operating-system-level constraints with zero daemon overhead:

- **Linux (`bwrap` / Bubblewrap):**
  - Isolates child processes within private Linux namespaces (`--unshare-all`).
  - System directories (`/usr`, `/bin`, `/lib`) are mounted read-only (`--ro-bind`).
  - Temporary files are isolated in an in-memory `tmpfs`.
  - Disables networking by default unless `--sandbox-net` is specified.
- **macOS (`setrlimit`):**
  - Restricts process execution limits (CPU runtime bounds) inside a `preexec_fn` hook before calling `exec`.

### Example
```bash
# Run untrusted C++ code with native OS restrictions
run solution.cpp --restrict
```

---

## 2. Containerized Sandboxing (`--sandbox`)

The `--sandbox` flag provides complete container virtualization using **Docker** or **Podman**.

```bash
# Run Python script inside an isolated container
run script.py --sandbox

# Enable network access for packages/downloads
run script.py --sandbox --sandbox-net
```

### Key Security Features
- **Privilege Dropping:** Runs with `--cap-drop=ALL` and `--security-opt=no-new-privileges`.
- **Isolated Mounts:** Mounts current workspace into the container with minimal permissions, and isolates `/tmp` in private in-memory storage.
- **Cross-Platform Compilation:** Compiles AND runs inside the container on macOS/Windows, avoiding architecture mismatches (`Exec format error`).
- **Host Path Isolation:** Automatically avoids host virtual environments and host-specific binary paths.

---

## 3. Configuration in `Run.toml`

Customize sandbox behaviors directly in your project's `Run.toml`.

### Global Sandboxing
Enable sandboxing globally for all executions in the project:
```toml
[core]
sandbox = true
```

### Custom Container Environments (`[sandbox]`)

```toml
[sandbox]
# 1. Custom Image Override
image = "alpine:edge"

# 2. Local Dockerfile Auto-Building
# Automatically builds and tags the image using content hashing (rebuilds only on changes)
dockerfile = "./Dockerfile.dev"

# 3. Docker Compose Orchestration
# Manages `docker compose up -d` lifecycle and dispatches runs via `docker compose exec`
compose = "docker-compose.yml"
compose_service = "app"
```

### Task-Level Sandboxing
Isolate individual tasks defined under `[tasks]`:
```toml
[tasks]
build = "cargo build --release"

[tasks.test]
command = "pytest tests/ -v"
sandbox = true
```

---

## 4. Persistent Watch Containers (`--watch`)

When combining `--watch` with `--sandbox`, running a cold `docker run` on every file modification can add hundreds of milliseconds of overhead.

`run` optimizes this workflow by maintaining a long-running background **"sleeper" container** (`tail -f /dev/null`) during the watch session:
- **Instant Reloads:** File modifications execute via `docker exec` against the warm container, reducing cycle time from ~900ms down to ~50ms.
- **Automatic Teardown:** Signal traps (`SIGINT`, `SIGTERM`) ensure the background container is cleanly removed when the watch process terminates.

```bash
# Ultra-fast sandboxed live development
run app.py --sandbox --watch
```
