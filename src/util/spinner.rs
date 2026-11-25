//! Animated Braille spinners and inline progress bars for interactive TTYs.
//!
//! Renders Node-tool (`ora` / `vite` / `clack`) style animations using
//! `crossterm` when [`UiMode::Modern`] is active on an interactive TTY.
//! Automatically becomes a zero-overhead no-op in [`UiMode::Plain`],
//! [`UiMode::Tui`], [`Verbosity::Quiet`], or non-TTY piped environments.

use std::io::{self, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::model::options::UiMode;
use crate::util::output::{Colors, Printer, TagColor, Verbosity};

/// Braille spinner frames inspired by Node's `ora` / `cli-spinners`.
pub const BRAILLE_FRAMES: &[&str] = &["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

/// Frame interval for smooth CLI animations (`80ms` ≈ 12.5 FPS).
const TICK_INTERVAL: Duration = Duration::from_millis(80);

/// Animated CLI spinner with live elapsed timer.
pub struct Spinner {
    /// Printer controlling mode, colors, and verbosity.
    printer: Printer,
    /// Shared active message label.
    label: Arc<Mutex<String>>,
    /// Signals the background ticker thread to stop.
    stopped: Arc<AtomicBool>,
    /// Temporarily suppresses frame rendering while a child process owns stdio.
    paused: Arc<AtomicBool>,
    /// Tracks whether `finish_*` was already called.
    finished: bool,
    /// Start timestamp for elapsed duration display.
    started_at: Instant,
    /// Background ticker thread handle (`None` when animation is disabled).
    handle: Option<JoinHandle<()>>,
}

impl Spinner {
    /// Start an animated spinner if `printer` is in [`UiMode::Modern`] on a TTY.
    pub fn start(printer: Printer, message: impl Into<String>) -> Self {
        let msg = message.into();
        let active = printer.ui_mode() == UiMode::Modern
            && printer.verbosity() >= Verbosity::Info
            && Colors::is_stdout_tty()
            && !printer.has_tui_sink();

        let label = Arc::new(Mutex::new(msg));
        let stopped = Arc::new(AtomicBool::new(false));
        let paused = Arc::new(AtomicBool::new(false));
        let started_at = Instant::now();

        let handle = if active {
            let label_ref = Arc::clone(&label);
            let stopped_ref = Arc::clone(&stopped);
            let paused_ref = Arc::clone(&paused);
            let colors = printer.colors();

            Some(thread::spawn(move || {
                let mut frame_idx: usize = 0;
                while !stopped_ref.load(Ordering::Relaxed) {
                    if !paused_ref.load(Ordering::Relaxed) {
                        let text = label_ref
                            .lock()
                            .map(|g| g.clone())
                            .unwrap_or_default();
                        let glyph = BRAILLE_FRAMES[frame_idx % BRAILLE_FRAMES.len()];
                        let elapsed = started_at.elapsed().as_secs_f64();
                        let rail = colors.style_text("│", TagColor::Gray, false);
                        let spin = colors.style_text(glyph, TagColor::Cyan, true);
                        let time_str = colors.style_text(&format!("{elapsed:.1}s"), TagColor::Gray, false);
                        let mut out = io::stdout();
                        let _ = write!(out, "\r\x1b[2K{rail}  {spin} {text} {time_str}");
                        let _ = out.flush();
                        frame_idx = frame_idx.wrapping_add(1);
                    }
                    thread::sleep(TICK_INTERVAL);
                }
            }))
        } else {
            None
        };

        Self {
            printer,
            label,
            stopped,
            paused,
            finished: false,
            started_at,
            handle,
        }
    }

    /// Update the spinner label while running.
    pub fn set_message(&self, message: impl Into<String>) {
        if let Ok(mut guard) = self.label.lock() {
            *guard = message.into();
        }
    }

    /// Clear the active spinner line and pause rendering before child stdio runs.
    pub fn pause(&self) {
        if self.handle.is_some() {
            self.paused.store(true, Ordering::Relaxed);
            clear_line();
        }
    }

    /// Stop the spinner cleanly and print a success rail step (`│  ✔ <message> (X.XXs)`).
    pub fn finish_ok(&mut self, message: &str) {
        self.stop_thread();
        if self.printer.ui_mode() == UiMode::Modern && self.printer.verbosity() >= Verbosity::Info {
            let elapsed = self.started_at.elapsed().as_secs_f64();
            let suffix = self
                .printer
                .colors()
                .style_text(&format!("({elapsed:.2}s)"), TagColor::Gray, false);
            self.printer
                .status("PASS", &format!("{message} {suffix}"), TagColor::Green);
        }
        self.finished = true;
    }

    /// Stop the spinner cleanly and print an error rail step (`│  ✖ <message>`).
    pub fn finish_err(&mut self, message: &str) {
        self.stop_thread();
        if self.printer.ui_mode() == UiMode::Modern {
            self.printer.error(message);
        }
        self.finished = true;
    }

    /// Stop the background ticker thread and clear the terminal line.
    fn stop_thread(&mut self) {
        self.stopped.store(true, Ordering::Relaxed);
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
            clear_line();
        }
    }
}

impl Drop for Spinner {
    /// Ensure any active spinner thread is stopped and the terminal line is cleared.
    fn drop(&mut self) {
        if !self.finished {
            self.stop_thread();
        }
    }
}

/// Inline progress bar for `--test-dir` batch execution in [`UiMode::Modern`].
pub struct ProgressBar {
    /// Printer controlling mode and color styling.
    printer: Printer,
    /// Total number of items to process.
    total: usize,
    /// Start timestamp for batch duration reporting.
    started_at: Instant,
}

impl ProgressBar {
    /// Create a [`ProgressBar`] for `total` items.
    pub fn new(printer: Printer, total: usize) -> Self {
        Self {
            printer,
            total: total.max(1),
            started_at: Instant::now(),
        }
    }

    /// Render an updated progress frame (`│  ⠸ [████████░░] 8/10 · case.in`) on TTY.
    pub fn update(&self, current: usize, label: &str) {
        if self.printer.ui_mode() != UiMode::Modern
            || self.printer.verbosity() < Verbosity::Info
            || !Colors::is_stdout_tty()
            || self.printer.has_tui_sink()
        {
            return;
        }
        let width: usize = 16;
        let clamped = current.min(self.total);
        let filled = (clamped * width) / self.total;
        let empty = width.saturating_sub(filled);
        let bar = format!("{}{}", "█".repeat(filled), "░".repeat(empty));
        let glyph = BRAILLE_FRAMES[current % BRAILLE_FRAMES.len()];

        let colors = self.printer.colors();
        let rail = colors.style_text("│", TagColor::Gray, false);
        let spin = colors.style_text(glyph, TagColor::Cyan, true);
        let styled_bar = colors.style_text(&bar, TagColor::Cyan, false);
        let counter = colors.style_text(&format!("{clamped}/{}", self.total), TagColor::Gray, false);

        let mut out = io::stdout();
        let _ = write!(out, "\r\x1b[2K{rail}  {spin} [{styled_bar}] {counter} · {label}");
        let _ = out.flush();
    }

    /// Clear the active inline progress bar line on TTY without printing a summary.
    pub fn clear(&self) {
        if self.printer.ui_mode() == UiMode::Modern
            && Colors::is_stdout_tty()
            && !self.printer.has_tui_sink()
        {
            clear_line();
        }
    }

    /// Clear the inline progress bar and print the batch completion summary.
    pub fn finish(&self, passed: usize, failed: usize) {
        if self.printer.ui_mode() != UiMode::Modern || self.printer.has_tui_sink() {
            return;
        }
        if Colors::is_stdout_tty() {
            clear_line();
        }
        let elapsed = self.started_at.elapsed().as_secs_f64();
        let colors = self.printer.colors();
        let corner = colors.style_text("╰", TagColor::Gray, false);
        let pass_txt = colors.style_text(&format!("✔ {passed} passed"), TagColor::Green, true);
        let fail_txt = if failed > 0 {
            colors.style_text(&format!(" · ✖ {failed} failed"), TagColor::Red, true)
        } else {
            String::new()
        };
        let dur_txt = colors.style_text(&format!("({elapsed:.2}s)"), TagColor::Gray, false);
        println!("{corner}  {pass_txt}{fail_txt} {dur_txt}");
    }
}

/// Clear the current stdout line using ANSI carriage-return + erase-line.
fn clear_line() {
    let mut out = io::stdout();
    let _ = write!(out, "\r\x1b[2K");
    let _ = out.flush();
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Verify Spinner is a safe no-op in Plain mode without TTY or panic.
    #[test]
    fn test_spinner_plain_mode_noop() {
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let mut spinner = Spinner::start(printer, "Compiling main.c");
        spinner.set_message("Linking");
        spinner.pause();
        spinner.finish_ok("Compiled main.c");
    }

    /// Verify ProgressBar formats cleanly without panicking on zero or full counts.
    #[test]
    fn test_progress_bar_plain_noop() {
        let printer = Printer::with_colors(Verbosity::Info, Colors::new(false));
        let bar = ProgressBar::new(printer, 0);
        bar.update(1, "1.in");
        bar.finish(1, 0);
    }
}
