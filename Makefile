# ==============================================================================
# Run - Fast, Intelligent Multi-Language Code Runner
# Standard POSIX Makefile
# ==============================================================================

# Installation Paths (Default: ~/.local or /usr/local via PREFIX=/usr/local)
PREFIX       ?= $(HOME)/.local
BINDIR        = $(PREFIX)/bin
MANDIR        = $(PREFIX)/share/man/man1
ZSHDIR        = $(PREFIX)/share/zsh/site-functions
BASHDIR       = $(PREFIX)/share/bash-completion/completions
FISHDIR       = $(PREFIX)/share/fish/vendor_completions.d

# Python & Environment
PYTHON       ?= python3
VENV          = .venv
VENV_BIN      = $(VENV)/bin
VENV_PY       = $(VENV_BIN)/python
VENV_PIP      = $(VENV_BIN)/pip

# Distribution & Build
DIST_DIR      = dist
DIST_BIN      = $(DIST_DIR)/run
COMPLETIONS   = completions

# Colors for Terminal UI
BOLD          = \033[1m
CYAN          = \033[36m
GREEN         = \033[32m
YELLOW        = \033[33m
RED           = \033[31m
RESET         = \033[0m

.PHONY: all help venv test dist completions install uninstall clean

# Default Target: Print Help
all: help

## help: Display available make targets and descriptions
help:
	@printf "$(BOLD)Run Build & Installation System$(RESET)\n"
	@printf "Usage: make [target] [PREFIX=/path/to/prefix]\n\n"
	@printf "$(BOLD)Available Targets:$(RESET)\n"
	@printf "  $(CYAN)install$(RESET)      Smart install (standalone binary if built, or venv runner) + man + completions\n"
	@printf "  $(CYAN)uninstall$(RESET)    Remove binary, man page, and completions from PREFIX\n"
	@printf "  $(CYAN)dist$(RESET)         Compile standalone native binary using Nuitka into dist/run\n"
	@printf "  $(CYAN)test$(RESET)         Run automated test suite via pytest\n"
	@printf "  $(CYAN)venv$(RESET)         Create local Python virtual environment (.venv) and install dependencies\n"
	@printf "  $(CYAN)completions$(RESET)  Generate shell completion scripts (Zsh, Bash, Fish)\n"
	@printf "  $(CYAN)clean$(RESET)        Remove build artifacts, cache files, and dist directories\n"
	@printf "\n$(BOLD)Configuration Variables:$(RESET)\n"
	@printf "  PREFIX       Installation prefix (default: $(PREFIX))\n"
	@printf "  PYTHON       Python interpreter to use (default: $(PYTHON))\n"

## venv: Initialize development virtualenv and install package
venv:
	@printf "$(GREEN)[ VENV ]$(RESET) Initializing virtual environment...\n"
	@if [ ! -d "$(VENV)" ]; then \
		$(PYTHON) -m venv $(VENV); \
	fi
	@$(VENV_PIP) install --quiet --upgrade pip
	@$(VENV_PIP) install --quiet -e ".[dev]"
	@printf "$(GREEN)[ VENV ]$(RESET) Virtual environment ready at $(VENV)\n"

## test: Run pytest test suite
test:
	@printf "$(GREEN)[ TEST ]$(RESET) Running test suite...\n"
	@if [ -x "$(VENV_PY)" ]; then \
		$(VENV_PY) -m pytest -p no:cacheprovider tests/; \
	else \
		$(PYTHON) -m pytest -p no:cacheprovider tests/; \
	fi

## completions: Generate shell completion files for zsh, bash, fish
completions:
	@printf "$(GREEN)[ COMPLETIONS ]$(RESET) Generating shell completions...\n"
	@mkdir -p $(COMPLETIONS)
	@if [ -x "$(VENV_PY)" ]; then \
		$(VENV_PY) src/main.py --completion zsh > $(COMPLETIONS)/_run; \
		$(VENV_PY) src/main.py --completion bash > $(COMPLETIONS)/run; \
		$(VENV_PY) src/main.py --completion fish > $(COMPLETIONS)/run.fish; \
	else \
		$(PYTHON) src/main.py --completion zsh > $(COMPLETIONS)/_run; \
		$(PYTHON) src/main.py --completion bash > $(COMPLETIONS)/run; \
		$(PYTHON) src/main.py --completion fish > $(COMPLETIONS)/run.fish; \
	fi
	@printf "$(GREEN)[ COMPLETIONS ]$(RESET) Generated in $(COMPLETIONS)/\n"

## dist: Build standalone native binary using Nuitka
dist:
	@printf "$(GREEN)[ DIST ]$(RESET) Compiling standalone native executable via Nuitka...\n"
	@mkdir -p $(DIST_DIR)
	@if [ -x "$(VENV_PY)" ]; then \
		$(VENV_PY) -m nuitka --onefile --lto=yes --remove-output --output-dir=$(DIST_DIR) --output-filename=run src/main.py; \
	else \
		$(PYTHON) -m nuitka --onefile --lto=yes --remove-output --output-dir=$(DIST_DIR) --output-filename=run src/main.py; \
	fi
	@printf "$(GREEN)[ DIST ]$(RESET) Built standalone binary: $(DIST_BIN)\n"

## install: Install binary, man pages, and shell completions
install: completions
	@printf "$(GREEN)[ INSTALL ]$(RESET) Installing to $(PREFIX)...\n"
	@install -d $(BINDIR) $(MANDIR) $(ZSHDIR) $(BASHDIR) $(FISHDIR)
	@if [ -f "$(DIST_BIN)" ]; then \
		printf "$(GREEN)[ INSTALL ]$(RESET) Installing standalone binary $(DIST_BIN) -> $(BINDIR)/run\n"; \
		install -m 755 $(DIST_BIN) $(BINDIR)/run; \
	else \
		printf "$(YELLOW)[ INSTALL ]$(RESET) No standalone binary in dist/ - creating virtualenv runner script...\n"; \
		if [ ! -d "$(VENV)" ]; then $(MAKE) venv; fi; \
		printf '#!/usr/bin/env bash\nexec "%s/%s" "%s/src/main.py" "$$@"\n' "$$(pwd)" "$(VENV_PY)" "$$(pwd)" > $(BINDIR)/run; \
		chmod 755 $(BINDIR)/run; \
	fi
	@if [ -f "docs/man/run.1" ]; then \
		printf "$(GREEN)[ INSTALL ]$(RESET) Installing manual page -> $(MANDIR)/run.1\n"; \
		install -m 644 docs/man/run.1 $(MANDIR)/run.1; \
	fi
	@if [ -f "$(COMPLETIONS)/_run" ]; then \
		printf "$(GREEN)[ INSTALL ]$(RESET) Installing Zsh completions -> $(ZSHDIR)/_run\n"; \
		install -m 644 $(COMPLETIONS)/_run $(ZSHDIR)/_run; \
	fi
	@if [ -f "$(COMPLETIONS)/run" ]; then \
		printf "$(GREEN)[ INSTALL ]$(RESET) Installing Bash completions -> $(BASHDIR)/run\n"; \
		install -m 644 $(COMPLETIONS)/run $(BASHDIR)/run; \
	fi
	@if [ -f "$(COMPLETIONS)/run.fish" ]; then \
		printf "$(GREEN)[ INSTALL ]$(RESET) Installing Fish completions -> $(FISHDIR)/run.fish\n"; \
		install -m 644 $(COMPLETIONS)/run.fish $(FISHDIR)/run.fish; \
	fi
	@printf "$(GREEN)[ SUCCESS ]$(RESET) Run installed successfully to $(BINDIR)/run\n"

## uninstall: Remove all installed artifacts
uninstall:
	@printf "$(RED)[ UNINSTALL ]$(RESET) Removing from $(PREFIX)...\n"
	@rm -f $(BINDIR)/run
	@rm -f $(MANDIR)/run.1
	@rm -f $(ZSHDIR)/_run
	@rm -f $(BASHDIR)/run
	@rm -f $(FISHDIR)/run.fish
	@printf "$(GREEN)[ SUCCESS ]$(RESET) Run uninstalled cleanly from $(PREFIX)\n"

## clean: Clean up temporary files, caches, and build artifacts
clean:
	@printf "$(YELLOW)[ CLEAN ]$(RESET) Cleaning build artifacts and cache files...\n"
	@rm -rf $(DIST_DIR) $(COMPLETIONS) build/ *.egg-info .pytest_cache .run_cache
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@printf "$(GREEN)[ CLEAN ]$(RESET) Workspace cleaned.\n"
