# Run - Future Ideas & Roadmap Backlog

This document captures detailed architectural proposals, security models, and packaging strategies for upcoming major features in `run`.

---

## 1. Sandboxed Execution (`run --sandbox`)
- **Category**: Security & Untrusted Code Execution (Competitive Programming / Education)
- **Status**: Planned / Roadmap
- **Problem Statement**:
  When running untrusted code (e.g. downloaded solutions from competitive programming platforms, student homework submissions, or AI-generated scripts), malicious code could delete files, exfiltrate data over the network, or perform fork bombs.
- **Key Capabilities**:
  - **`--sandbox` Flag**: Launches execution inside an isolated environment.
  - **Network Isolation**: Disables network socket creation (blocks HTTP/TCP/UDP traffic).
  - **Filesystem Isolation**: Mounts the system root as read-only, providing only an ephemeral scratch workspace (`/tmp` or tmpfs).
  - **Process & Resource Limits**: Enforces hard caps on CPU execution time, memory allocation, and maximum process count to prevent fork bombs.
  - **Multi-Tiered Cross-Platform Backend**:
    - **Linux**: Bubblewrap (`bwrap`) or kernel namespaces + `seccomp`.
    - **macOS**: `sandbox-exec` profiles + temporary directory isolation.
    - **Windows**: Windows Job Objects with restricted security tokens.
    - **Fallback**: POSIX `setrlimit` / `chroot` for environments lacking privileged sandbox tools.

---

## 2. Makefile for Setup & Installation (`make install`)
- **Category**: Toolchain Integration & Packaging
- **Status**: Planned / Roadmap
- **Problem Statement**:
  Users on POSIX systems (Linux, macOS, BSD) expect standard `make install` and `make uninstall` workflows that install the executable, the manual page (`man 1 run`), and shell completions into standard system paths (`PREFIX`).
- **Key Capabilities**:
  - **Configurable `PREFIX`**: Defaults to `~/.local` (user-level) or `/usr/local` (system-wide).
  - **Target Reference**:
    ```makefile
    PREFIX ?= $(HOME)/.local
    BINDIR = $(PREFIX)/bin
    MANDIR = $(PREFIX)/share/man/man1
    ZSHDIR = $(PREFIX)/share/zsh/site-functions
    BASHDIR = $(PREFIX)/share/bash-completion/completions
    FISHDIR = $(PREFIX)/share/fish/vendor_completions.d

    install:
        install -d $(BINDIR) $(MANDIR)
        install -m 755 bin/run $(BINDIR)/run
        install -m 644 docs/man/run.1 $(MANDIR)/run.1
        # Install shell completions...

    uninstall:
        rm -f $(BINDIR)/run $(MANDIR)/run.1
    ```
  - **Standard Lifecycle Targets**: `install`, `uninstall`, `test` (`pytest`), `clean`, `dist`.

---

## 3. Standalone Executable (`make dist` & Release CI)
- **Category**: Distribution & Portability
- **Status**: Planned / Roadmap
- **Problem Statement**:
  Requiring users to have Python 3.11+ and a virtual environment installed adds friction on minimal Docker containers, CI runners, or machines without developer runtimes.
- **Key Capabilities**:
  - **Single Binary Packaging**: Build self-contained executables (`dist/run` on Linux/macOS, `dist/run.exe` on Windows) via PyInstaller or Nuitka.
  - **Zero Python Runtime Dependency**: Users can download a single binary, `chmod +x run`, and execute it immediately.
  - **Local Build Target**: `make dist` packages the binary locally.
  - **Multi-Platform GitHub Actions Matrix**:
    - `linux-x86_64` (glibc and musl)
    - `darwin-arm64` (Apple Silicon M1/M2/M3/M4)
    - `darwin-x86_64` (Intel Mac)
    - `windows-x64` (`run.exe`)
