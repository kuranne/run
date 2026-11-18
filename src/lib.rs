//! Core library for the `run` CLI compiler and runner.
//!
//! Architecture:
//! - `util`: Cross-cutting infrastructure including unified errors (`errors`),
//!   compile-time `Cargo.toml` metadata and `.env` loading (`env`), `Run.toml`
//!   configuration discovery and validation (`config`), ANSI/plain tagged
//!   output and unified diffs via `similar` (`output`), gitignore-style glob
//!   matching via `regex` (`glob`), and variable/environment substitution with
//!   `shlex` quoting (`substitutions`).
//! - `runner`: Language dispatch, execution, and project/task/test orchestration
//!   (populated in subsequent milestones).

#![warn(missing_docs)]

pub mod runner;
pub mod util;
