//! CLI binary entrypoint for `run`.
//!
//! Keeps `src/main.rs` strictly limited to CLI parsing (`clap` derive) and
//! high-level dispatch into the `run` library (`src/lib.rs`).

use std::process::ExitCode;

use clap::Parser;

/// Command-line interface definition powered by `clap` derive macros.
#[derive(Debug, Parser)]
#[command(
    name = "run",
    version = env!("CARGO_PKG_VERSION"),
    about = "Professional Auto Compiler & Runner"
)]
struct Cli {
    /// Target source files, task name, or template target.
    #[arg(value_name = "FILES")]
    _files: Vec<String>,

    /// Silence compiler output and informational logs.
    #[arg(short = 'q', long = "quiet")]
    _quiet: bool,

    /// Increase verbosity (-v for debug logs, -vv for trace diagnostics).
    #[arg(short = 'v', long = "verbose", action = clap::ArgAction::Count)]
    _verbose: u8,

    /// Disable ANSI color codes in output.
    #[arg(long = "no-color")]
    _no_color: bool,
}

/// Entrypoint for the `run` CLI binary.
fn main() -> ExitCode {
    let _cli = Cli::parse();
    ExitCode::SUCCESS
}
