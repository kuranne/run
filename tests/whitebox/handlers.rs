//! Cross-module white-box tests for [`HandlerRegistry`] and built-in handlers.

use std::fs;
use std::sync::Arc;

use mockall::Sequence;

use run::core::CompilerRunner;
use run::model::config::Config;
use run::model::options::RunnerOptions;
use run::util::output::{Colors, Printer, Verbosity};

use super::helpers::{ok_outcome, MockCmdExec, TempWorkspace};

/// Verify `RustHandler` compiles standalone `.rs` with `--compiler` override and runs it.
#[test]
fn test_rust_handler_compile_and_run_via_mock_executor() {
    let ws = TempWorkspace::new("rust_handler");
    let src = ws.path().join("hello.rs");
    fs::write(&src, "fn main() {}\n").unwrap();

    let printer = Printer::with_colors(Verbosity::Quiet, Colors::new(false));
    let mut runner = CompilerRunner::with_cache_base(
        RunnerOptions {
            compiler: Some("rustc-nightly".to_string()),
            ..Default::default()
        },
        Some("-O"),
        Some("--hello"),
        Config::default(),
        printer,
        ws.path(),
        &ws.path().join("cache"),
    )
    .expect("runner");

    let mut seq = Sequence::new();
    let mut mock = MockCmdExec::new();
    mock.expect_execute()
        .times(1)
        .in_sequence(&mut seq)
        .withf(|spec| {
            spec.compiling && spec.cmd[0] == "rustc-nightly" && spec.cmd.contains(&"-O".to_string())
        })
        .returning(|spec| {
            let out_idx = spec
                .cmd
                .iter()
                .position(|a| a == "-o")
                .expect("-o flag present");
            fs::write(&spec.cmd[out_idx + 1], b"FAKE_RUST_BIN").unwrap();
            ok_outcome("")
        });
    mock.expect_execute()
        .times(1)
        .in_sequence(&mut seq)
        .withf(|spec| !spec.compiling && spec.cmd.last().map(String::as_str) == Some("--hello"))
        .returning(|_| ok_outcome(""));
    runner.base.set_executor(Arc::new(mock));

    assert!(runner.compile_and_run(&[src], false));
}

/// Verify extensionless C file with `#include` is dispatched to `CFamilyHandler` via shebang/content detection.
#[test]
fn test_extensionless_c_dispatch_via_mock_executor() {
    let ws = TempWorkspace::new("extless_c");
    let src = ws.path().join("script_no_ext");
    fs::write(
        &src,
        "#!/usr/bin/env run\n#include <stdio.h>\nint main(void) { return 0; }\n",
    )
    .unwrap();

    let printer = Printer::with_colors(Verbosity::Quiet, Colors::new(false));
    let mut runner = CompilerRunner::with_cache_base(
        RunnerOptions {
            no_cache: true,
            out_dir: Some(ws.path().to_string_lossy().into_owned()),
            ..Default::default()
        },
        None,
        None,
        Config::default(),
        printer,
        ws.path(),
        &ws.path().join("cache"),
    )
    .expect("runner");

    let mut seq = Sequence::new();
    let mut mock = MockCmdExec::new();
    mock.expect_execute()
        .times(1)
        .in_sequence(&mut seq)
        .withf(|spec| {
            spec.compiling && spec.cmd[0] == "gcc" && spec.cmd.iter().any(|a| a.ends_with(".c"))
        })
        .returning(|spec| {
            let out_idx = spec
                .cmd
                .iter()
                .position(|a| a == "-o")
                .expect("-o flag present");
            fs::write(&spec.cmd[out_idx + 1], b"FAKE_C_BIN").unwrap();
            ok_outcome("")
        });
    mock.expect_execute()
        .times(1)
        .in_sequence(&mut seq)
        .withf(|spec| !spec.compiling)
        .returning(|_| ok_outcome(""));
    runner.base.set_executor(Arc::new(mock));

    assert!(runner.compile_and_run(&[src], false));
}
