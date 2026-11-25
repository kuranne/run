//! Unified cross-module white-box Rust integration test suite.
//!
//! Organizes `mockall`-based integration tests into focused submodules under
//! `tests/whitebox/` without spawning external compilers, runtimes, or
//! container daemons.

#[path = "whitebox/handlers.rs"]
mod handlers;
#[path = "whitebox/helpers.rs"]
mod helpers;
#[path = "whitebox/orchestration.rs"]
mod orchestration;
#[path = "whitebox/security_config.rs"]
mod security_config;
