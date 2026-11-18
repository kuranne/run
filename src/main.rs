//! CLI binary entrypoint for `run`.
//!
//! Keeps `src/main.rs` strictly limited to CLI parsing (`clap` derive) and
//! high-level dispatch into the `run` library (`src/lib.rs`).

#![warn(missing_docs)]

use std::path::Path;
use std::process::ExitCode;

use clap::Parser;
use run::util::config::Config;
use run::util::env::load_dotenv;
use run::util::errors::RunError;
use run::util::output::{Printer, Verbosity};

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
    files: Vec<String>,

    /// Silence compiler output and informational logs.
    #[arg(short = 'q', long = "quiet")]
    quiet: bool,

    /// Increase verbosity (-v for debug logs, -vv for trace diagnostics).
    #[arg(short = 'v', long = "verbose", action = clap::ArgAction::Count)]
    verbose: u8,

    /// Disable ANSI color codes in output.
    #[arg(long = "no-color")]
    no_color: bool,
}

/// Entrypoint for the `run` CLI binary.
fn main() -> ExitCode {
    let cli = Cli::parse();
    let verbosity = Verbosity::from_cli(cli.quiet, cli.verbose);
    let printer = Printer::init_global(verbosity, cli.no_color);

    if cli.verbose >= 1 && !cli.quiet {
        printer.debug("Debug logging enabled");
    }

    match run_cli(&cli) {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            printer.error(&err.to_string());
            ExitCode::FAILURE
        }
    }
}

/// Execute M1 startup initialization (`.env` loading and `Run.toml` discovery).
fn run_cli(cli: &Cli) -> Result<(), RunError> {
    let cwd = std::env::current_dir()?;
    let _dotenv_report = load_dotenv(&cwd)?;
    let _config = Config::load(Some(&cwd), None)?;
    if !cli.files.is_empty() {
        // M3/M4 will dispatch file, task, and project execution here.
        let _ = Path::new(&cli.files[0]);
    }
    Ok(())
}
