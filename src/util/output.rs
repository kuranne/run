//! Terminal output, color management, verbosity filtering, metrics, and unified diffs.
//!
//! Uses `crossterm::style` for cross-platform ANSI styling (`NO_COLOR` non-empty
//! or `--no-color` disables styling cleanly) and `similar::TextDiff` for
//! colorized unified diffs matching Python `difflib.unified_diff` output.

use std::io::{self, Write};
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};

use crossterm::style::{Attribute, Color, Stylize};
use crossterm::tty::IsTty;
use similar::TextDiff;

/// Global verbosity level stored atomically for [`Printer::global()`].
static GLOBAL_VERBOSITY: AtomicU8 = AtomicU8::new(Verbosity::Info as u8);

/// Global color enable flag stored atomically for [`Printer::global()`].
static GLOBAL_COLOR_ENABLED: AtomicBool = AtomicBool::new(true);

/// Tracks whether [`Printer::init_global`] has been called.
static GLOBAL_INITIALIZED: AtomicBool = AtomicBool::new(false);

/// Semantic tag/message colors mapped onto `crossterm::style::Color`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TagColor {
    /// Bright green (`PASS`, `COMPILE`, `RUN`, `CLEAN`, `BUILD`, `INIT`, `CREATE`, diff `+`).
    Green,
    /// Bright cyan (`INFO`, `TASK`, diff `@@`).
    Cyan,
    /// Bright yellow (`WARN`, `DRY-RUN`, metrics, diff headers).
    Yellow,
    /// Bright red (`ERROR`, `CRIT`, `FAIL`, diff `-`).
    Red,
    /// Dark gray (`DEBUG`, `SKIP`, separators, diff context).
    Gray,
    /// Default terminal foreground color.
    Reset,
}

impl TagColor {
    /// Map this semantic color to a `crossterm::style::Color`.
    fn to_crossterm(self) -> Option<Color> {
        match self {
            Self::Green => Some(Color::Green),
            Self::Cyan => Some(Color::Cyan),
            Self::Yellow => Some(Color::Yellow),
            Self::Red => Some(Color::Red),
            Self::Gray => Some(Color::DarkGrey),
            Self::Reset => None,
        }
    }
}

/// Color styling configuration respecting `--no-color` and `NO_COLOR`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Colors {
    /// Whether ANSI styling codes should be emitted.
    enabled: bool,
}

impl Colors {
    /// Construct a [`Colors`] instance with an explicit enable flag.
    pub const fn new(enabled: bool) -> Self {
        Self { enabled }
    }

    /// Resolve color enablement from `--no-color` and the process `NO_COLOR` variable.
    ///
    /// Follows `dev:src/util/output.py`: color is disabled when `no_color_flag`
    /// is `true` or `NO_COLOR` is set and non-empty.
    pub fn from_env(no_color_flag: bool) -> Self {
        let env_val = std::env::var_os("NO_COLOR");
        let non_empty = env_val.as_deref().is_some_and(|v| !v.is_empty());
        Self {
            enabled: !(no_color_flag || non_empty),
        }
    }

    /// Pure helper for testing `NO_COLOR` semantics without mutating process environment.
    pub fn from_no_color_env(no_color_flag: bool, no_color_env: Option<&str>) -> Self {
        let non_empty = no_color_env.is_some_and(|v| !v.is_empty());
        Self {
            enabled: !(no_color_flag || non_empty),
        }
    }

    /// Return `true` if ANSI color output is enabled.
    pub const fn is_enabled(self) -> bool {
        self.enabled
    }

    /// Check whether standard output is attached to an interactive terminal via `crossterm::tty::IsTty`.
    pub fn is_stdout_tty() -> bool {
        io::stdout().is_tty()
    }

    /// Style `text` with `color` and optional bold attribute using `crossterm::style::Stylize`.
    pub fn style_text(self, text: &str, color: TagColor, bold: bool) -> String {
        if !self.enabled {
            return text.to_string();
        }
        match (color.to_crossterm(), bold) {
            (Some(c), true) => text.with(c).attribute(Attribute::Bold).to_string(),
            (Some(c), false) => text.with(c).to_string(),
            (None, true) => text.attribute(Attribute::Bold).to_string(),
            (None, false) => text.to_string(),
        }
    }
}

/// CLI output verbosity levels matching `-q`, default, `-v`, and `-vv`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Default)]
#[repr(u8)]
pub enum Verbosity {
    /// Quiet (`-q`): suppress informational logs and action tags, keeping errors, `PASS`/`FAIL`, and metrics.
    Quiet = 0,
    /// Default level: emit info, warnings, errors, and action tags.
    #[default]
    Info = 1,
    /// Verbose (`-v`): additionally emit `[ DEBUG ]` messages.
    Verbose = 2,
    /// Trace (`-vv`): emit debug messages plus detailed diagnostics.
    Debug = 3,
}

impl Verbosity {
    /// Map CLI `--quiet` and `-v` count flags to [`Verbosity`].
    pub const fn from_cli(quiet: bool, verbose: u8) -> Self {
        if quiet {
            Self::Quiet
        } else {
            match verbose {
                0 => Self::Info,
                1 => Self::Verbose,
                _ => Self::Debug,
            }
        }
    }

    /// Convert a raw `u8` atomic representation back to [`Verbosity`].
    const fn from_u8(val: u8) -> Self {
        match val {
            0 => Self::Quiet,
            1 => Self::Info,
            2 => Self::Verbose,
            _ => Self::Debug,
        }
    }
}

/// Tagged terminal printer with configurable verbosity and ANSI color support.
#[derive(Debug, Clone, Copy)]
pub struct Printer {
    /// Active verbosity threshold.
    verbosity: Verbosity,
    /// Active color policy.
    colors: Colors,
}

impl Printer {
    /// Create a [`Printer`] from `verbosity` and CLI `--no-color` flag (also consulting `NO_COLOR`).
    pub fn new(verbosity: Verbosity, no_color: bool) -> Self {
        Self {
            verbosity,
            colors: Colors::from_env(no_color),
        }
    }

    /// Create a [`Printer`] with an explicit [`Colors`] policy (useful for deterministic unit tests).
    pub const fn with_colors(verbosity: Verbosity, colors: Colors) -> Self {
        Self { verbosity, colors }
    }

    /// Initialize the global atomic [`Printer`] state once at CLI startup and return it.
    pub fn init_global(verbosity: Verbosity, no_color: bool) -> Self {
        let colors = Colors::from_env(no_color);
        GLOBAL_VERBOSITY.store(verbosity as u8, Ordering::Relaxed);
        GLOBAL_COLOR_ENABLED.store(colors.is_enabled(), Ordering::Relaxed);
        GLOBAL_INITIALIZED.store(true, Ordering::Relaxed);
        Self { verbosity, colors }
    }

    /// Return a [`Printer`] reflecting global state (or defaults if [`Self::init_global`] was not called).
    pub fn global() -> Self {
        if !GLOBAL_INITIALIZED.load(Ordering::Relaxed) {
            return Self::new(Verbosity::Info, false);
        }
        Self {
            verbosity: Verbosity::from_u8(GLOBAL_VERBOSITY.load(Ordering::Relaxed)),
            colors: Colors::new(GLOBAL_COLOR_ENABLED.load(Ordering::Relaxed)),
        }
    }

    /// Return the printer's active [`Verbosity`].
    pub const fn verbosity(self) -> Verbosity {
        self.verbosity
    }

    /// Return the printer's active [`Colors`] policy.
    pub const fn colors(self) -> Colors {
        self.colors
    }

    /// Return `true` if trace-level diagnostics (`-vv`) are enabled.
    pub const fn is_trace(self) -> bool {
        (self.verbosity as u8) >= (Verbosity::Debug as u8)
    }

    /// Format a tagged log line `[ TAG ] message`.
    pub fn format_tag(self, tag: &str, message: &str, color: TagColor) -> String {
        let badge = format!("[ {tag} ]");
        let styled_badge = self.colors.style_text(&badge, color, true);
        format!("{styled_badge} {message}")
    }

    /// Write a tagged line to an arbitrary [`Write`] sink.
    fn write_tag(
        self,
        w: &mut impl Write,
        tag: &str,
        message: &str,
        color: TagColor,
    ) -> io::Result<()> {
        writeln!(w, "{}", self.format_tag(tag, message, color))
    }

    /// Print an `[ ERROR ]` line to stdout (always emitted, even in quiet mode).
    pub fn error(self, message: &str) {
        let _ = self.error_to(&mut io::stdout(), message);
    }

    /// Write an `[ ERROR ]` line to `w` (always emitted, even in quiet mode).
    pub fn error_to(self, w: &mut impl Write, message: &str) -> io::Result<()> {
        self.write_tag(w, "ERROR", message, TagColor::Red)
    }

    /// Print a `[ WARN ]` line to stdout when verbosity is at least [`Verbosity::Info`].
    pub fn warning(self, message: &str) {
        let _ = self.warning_to(&mut io::stdout(), message);
    }

    /// Write a `[ WARN ]` line to `w` when verbosity is at least [`Verbosity::Info`].
    pub fn warning_to(self, w: &mut impl Write, message: &str) -> io::Result<()> {
        if self.verbosity >= Verbosity::Info {
            self.write_tag(w, "WARN", message, TagColor::Yellow)?;
        }
        Ok(())
    }

    /// Print an `[ INFO ]` line to stdout when verbosity is at least [`Verbosity::Info`].
    pub fn info(self, message: &str) {
        let _ = self.info_to(&mut io::stdout(), message);
    }

    /// Write an `[ INFO ]` line to `w` when verbosity is at least [`Verbosity::Info`].
    pub fn info_to(self, w: &mut impl Write, message: &str) -> io::Result<()> {
        if self.verbosity >= Verbosity::Info {
            self.write_tag(w, "INFO", message, TagColor::Cyan)?;
        }
        Ok(())
    }

    /// Print a `[ DEBUG ]` line to stdout when verbosity is at least [`Verbosity::Verbose`].
    pub fn debug(self, message: &str) {
        let _ = self.debug_to(&mut io::stdout(), message);
    }

    /// Write a `[ DEBUG ]` line to `w` when verbosity is at least [`Verbosity::Verbose`].
    pub fn debug_to(self, w: &mut impl Write, message: &str) -> io::Result<()> {
        if self.verbosity >= Verbosity::Verbose {
            self.write_tag(w, "DEBUG", message, TagColor::Gray)?;
        }
        Ok(())
    }

    /// Print a custom action tag (e.g. `COMPILE`, `RUN`, `DRY-RUN`, `BUILD`, `SKIP`) to stdout.
    pub fn action(self, tag: &str, message: &str, color: TagColor) {
        let _ = self.action_to(&mut io::stdout(), tag, message, color);
    }

    /// Write a custom action tag to `w` when verbosity is at least [`Verbosity::Info`].
    pub fn action_to(
        self,
        w: &mut impl Write,
        tag: &str,
        message: &str,
        color: TagColor,
    ) -> io::Result<()> {
        if self.verbosity >= Verbosity::Info {
            self.write_tag(w, tag, message, color)?;
        }
        Ok(())
    }

    /// Print an always-visible status tag (e.g. `PASS`, `FAIL`) to stdout even in quiet mode.
    pub fn status(self, tag: &str, message: &str, color: TagColor) {
        let _ = self.status_to(&mut io::stdout(), tag, message, color);
    }

    /// Write an always-visible status tag (e.g. `PASS`, `FAIL`) to `w` even in quiet mode.
    pub fn status_to(
        self,
        w: &mut impl Write,
        tag: &str,
        message: &str,
        color: TagColor,
    ) -> io::Result<()> {
        self.write_tag(w, tag, message, color)
    }

    /// Format execution timing and peak memory metrics into the standard summary line.
    pub fn format_metrics(
        seconds: Option<f64>,
        memory_bytes: Option<u64>,
        colors: Colors,
    ) -> Option<String> {
        let mut parts = Vec::new();
        if let Some(secs) = seconds {
            parts.push(format!("Took {secs:.3}s"));
        }
        if let Some(bytes) = memory_bytes
            && bytes > 0
        {
            let mb = bytes as f64 / (1024.0 * 1024.0);
            if mb >= 1.0 {
                parts.push(format!("Peak Memory: {mb:.2} MB"));
            } else {
                let kb = bytes as f64 / 1024.0;
                parts.push(format!("Peak Memory: {kb:.1} KB"));
            }
        }
        if parts.is_empty() {
            return None;
        }
        let line = format!("  -> {}", parts.join(" | "));
        Some(colors.style_text(&line, TagColor::Yellow, false))
    }

    /// Print execution metrics (`Took X.XXXs | Peak Memory: ...`) to stdout.
    pub fn metrics(self, seconds: Option<f64>, memory_bytes: Option<u64>) {
        let _ = self.metrics_to(&mut io::stdout(), seconds, memory_bytes);
    }

    /// Write formatted execution metrics to `w`.
    pub fn metrics_to(
        self,
        w: &mut impl Write,
        seconds: Option<f64>,
        memory_bytes: Option<u64>,
    ) -> io::Result<()> {
        if let Some(line) = Self::format_metrics(seconds, memory_bytes, self.colors) {
            writeln!(w, "{line}")?;
        }
        Ok(())
    }

    /// Print execution duration (`Took X.XXXs`) to stdout.
    pub fn time(self, seconds: f64) {
        self.metrics(Some(seconds), None);
    }

    /// Print a visual separator line to stdout.
    pub fn separator(self) {
        let _ = self.separator_to(&mut io::stdout());
    }

    /// Write a visual separator line to `w`.
    pub fn separator_to(self, w: &mut impl Write) -> io::Result<()> {
        let bar = self.colors.style_text(&"-".repeat(30), TagColor::Gray, false);
        writeln!(w, "\n{bar}\n")
    }

    /// Print a colorized unified diff between `expected` and `actual` to stdout.
    pub fn diff(self, expected: &str, actual: &str, expected_name: &str) {
        let _ = self.diff_to(&mut io::stdout(), expected, actual, expected_name);
    }

    /// Write a colorized unified diff between `expected` and `actual` to `w`.
    ///
    /// Uses `similar::TextDiff::from_lines` with `.missing_newline_hint(false)`
    /// to match Python `difflib.unified_diff` output (`expected_name` vs
    /// `"actual_output"`). Emits nothing when `expected` and `actual` are identical.
    pub fn diff_to(
        self,
        w: &mut impl Write,
        expected: &str,
        actual: &str,
        expected_name: &str,
    ) -> io::Result<()> {
        let diff = TextDiff::from_lines(expected, actual);
        let mut udiff = diff.unified_diff();
        udiff
            .header(expected_name, "actual_output")
            .missing_newline_hint(false);
        let rendered = udiff.to_string();
        if rendered.is_empty() {
            return Ok(());
        }

        let header = self
            .colors
            .style_text("--- Differences ---", TagColor::Yellow, false);
        writeln!(w, "\n{header}")?;

        for line in rendered.lines() {
            let color = if line.starts_with('+') && !line.starts_with("+++") {
                TagColor::Green
            } else if line.starts_with('-') && !line.starts_with("---") {
                TagColor::Red
            } else if line.starts_with("@@") {
                TagColor::Cyan
            } else {
                TagColor::Gray
            };
            writeln!(w, "{}", self.colors.style_text(line, color, false))?;
        }

        let footer = self
            .colors
            .style_text("-------------------", TagColor::Yellow, false);
        writeln!(w, "{footer}\n")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper to capture output written to a UTF-8 buffer.
    fn render(f: impl FnOnce(&mut Vec<u8>)) -> String {
        let mut buf = Vec::new();
        f(&mut buf);
        String::from_utf8(buf).expect("valid utf8")
    }

    /// Verify `NO_COLOR` non-empty and `--no-color` rules.
    #[test]
    fn test_colors_no_color_semantics() {
        assert!(Colors::from_no_color_env(false, None).is_enabled());
        assert!(Colors::from_no_color_env(false, Some("")).is_enabled());
        assert!(!Colors::from_no_color_env(false, Some("1")).is_enabled());
        assert!(!Colors::from_no_color_env(false, Some("0")).is_enabled());
        assert!(!Colors::from_no_color_env(true, None).is_enabled());

        let plain = Colors::new(false).style_text("hello", TagColor::Green, true);
        assert_eq!(plain, "hello");
        assert!(!plain.contains('\x1b'));

        let colored = Colors::new(true).style_text("hello", TagColor::Green, true);
        assert!(colored.contains('\x1b'));
        assert!(colored.contains("hello"));
    }

    /// Verify verbosity gating for `Quiet`, `Info`, `Verbose`, and `Debug`.
    #[test]
    fn test_printer_verbosity_gating() {
        assert_eq!(Verbosity::from_cli(false, 0), Verbosity::Info);
        assert_eq!(Verbosity::from_cli(false, 1), Verbosity::Verbose);
        assert_eq!(Verbosity::from_cli(false, 2), Verbosity::Debug);
        assert_eq!(Verbosity::from_cli(true, 2), Verbosity::Quiet);

        let quiet = Printer::with_colors(Verbosity::Quiet, Colors::new(false));
        let quiet_out = render(|buf| {
            quiet.info_to(buf, "hidden info").unwrap();
            quiet.warning_to(buf, "hidden warn").unwrap();
            quiet.debug_to(buf, "hidden debug").unwrap();
            quiet
                .action_to(buf, "COMPILE", "hidden compile", TagColor::Green)
                .unwrap();
            quiet.status_to(buf, "PASS", "Test passed", TagColor::Green).unwrap();
            quiet.error_to(buf, "fatal").unwrap();
        });
        assert!(!quiet_out.contains("hidden"));
        assert!(quiet_out.contains("[ PASS ] Test passed"));
        assert!(quiet_out.contains("[ ERROR ] fatal"));

        let info = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let info_out = render(|buf| {
            info.info_to(buf, "visible info").unwrap();
            info.warning_to(buf, "visible warn").unwrap();
            info.debug_to(buf, "hidden debug").unwrap();
            info.action_to(buf, "DRY-RUN", "would run", TagColor::Yellow)
                .unwrap();
        });
        assert!(info_out.contains("[ INFO ] visible info"));
        assert!(info_out.contains("[ WARN ] visible warn"));
        assert!(info_out.contains("[ DRY-RUN ] would run"));
        assert!(!info_out.contains("hidden debug"));

        let verbose = Printer::with_colors(Verbosity::Verbose, Colors::new(false));
        assert!(!verbose.is_trace());
        let verbose_out = render(|buf| {
            verbose.debug_to(buf, "Debug logging enabled").unwrap();
        });
        assert!(verbose_out.contains("[ DEBUG ] Debug logging enabled"));

        let debug = Printer::with_colors(Verbosity::Debug, Colors::new(false));
        assert!(debug.is_trace());
    }

    /// Port of `dev:tests/test_core.py::test_printer_metrics` and `dev:tests/test_memory_tracking.py`.
    #[test]
    fn test_printer_metrics() {
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));

        let out1 = render(|buf| printer.metrics_to(buf, Some(0.042), None).unwrap());
        assert!(out1.contains("Took 0.042s"));
        assert!(!out1.contains("Peak Memory"));

        let out2 = render(|buf| printer.metrics_to(buf, None, Some(512 * 1024)).unwrap());
        assert!(out2.contains("Peak Memory: 512.0 KB"));

        let out3 = render(|buf| {
            printer
                .metrics_to(buf, None, Some(4 * 1024 * 1024))
                .unwrap()
        });
        assert!(out3.contains("Peak Memory: 4.00 MB"));

        let out4 = render(|buf| {
            printer
                .metrics_to(buf, None, Some((48.5 * 1024.0 * 1024.0) as u64))
                .unwrap()
        });
        assert!(out4.contains("Peak Memory: 48.50 MB"));

        let out5 = render(|buf| {
            printer
                .metrics_to(buf, Some(0.123), Some(2 * 1024 * 1024))
                .unwrap()
        });
        assert!(out5.contains("Took 0.123s | Peak Memory: 2.00 MB"));

        let out_zero = render(|buf| printer.metrics_to(buf, None, Some(0)).unwrap());
        assert!(out_zero.is_empty());
    }

    /// Verify separator and unified diff formatting via `similar::TextDiff`.
    #[test]
    fn test_separator_and_diff() {
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let sep = render(|buf| printer.separator_to(buf).unwrap());
        assert!(sep.contains("------------------------------"));

        let no_diff = render(|buf| {
            printer
                .diff_to(buf, "line1\nline2\n", "line1\nline2\n", "expected.out")
                .unwrap()
        });
        assert!(no_diff.is_empty());

        let has_diff = render(|buf| {
            printer
                .diff_to(buf, "hello\nworld\n", "hello\nrust\n", "test.out")
                .unwrap()
        });
        assert!(has_diff.contains("--- Differences ---"));
        assert!(has_diff.contains("--- test.out"));
        assert!(has_diff.contains("+++ actual_output"));
        assert!(has_diff.contains("-world"));
        assert!(has_diff.contains("+rust"));
        assert!(has_diff.contains("-------------------"));
        assert!(!has_diff.contains('\x1b'));
    }
}
