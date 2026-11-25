# Makefile for Run (Rust CLI & Core Library)

CARGO ?= cargo
PYTEST ?= pytest
PREFIX ?= $(HOME)/.local
BINDIR ?= $(PREFIX)/bin
MANDIR ?= $(PREFIX)/share/man/man1
COMPLETIONS_DIR ?= target/completions
TARGET_BIN ?= target/release/run

.PHONY: all build release test test-rust test-e2e lint doc completions install uninstall clean help

all: build

help:
	@echo "Available targets:"
	@echo "  make build       - Build debug binary (cargo build)"
	@echo "  make release     - Build optimized release binary (cargo build --release)"
	@echo "  make test        - Run Rust unit tests + black-box pytest CLI suite"
	@echo "  make test-rust   - Run Rust unit tests (cargo test)"
	@echo "  make test-e2e    - Run black-box pytest suite against compiled binary"
	@echo "  make lint        - Run clippy with -D warnings"
	@echo "  make doc         - Verify rustdoc with -D warnings"
	@echo "  make completions - Generate zsh/bash/fish completion scripts"
	@echo "  make install     - Build release binary and install to $(BINDIR)/run"
	@echo "  make uninstall   - Remove installed binary and man page"
	@echo "  make clean       - Remove build artifacts and caches"

build:
	$(CARGO) build

release:
	$(CARGO) build --release

test-rust:
	$(CARGO) test

test-e2e: build
	PYTHONPATH=.:tests RUN_BIN=$(CURDIR)/target/debug/run $(PYTEST) -p no:cacheprovider tests/

test: test-rust test-e2e

lint:
	$(CARGO) clippy --all-targets -- -D warnings

doc:
	RUSTDOCFLAGS="-D warnings" $(CARGO) doc --no-deps

completions:
	$(CARGO) xtask completions --out-dir $(COMPLETIONS_DIR)

install:
	$(CARGO) xtask install --prefix $(PREFIX)

uninstall:
	$(CARGO) xtask uninstall --prefix $(PREFIX)

clean:
	$(CARGO) clean
	@rm -rf $(COMPLETIONS_DIR) .pytest_cache
