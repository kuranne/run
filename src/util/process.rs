//! Cross-platform child process execution, process-tree termination, and peak RSS tracking.
//!
//! Adapted from Python's `src/util/process.py`. Provides [`MonitoredChild`] for
//! isolated process-group execution (`process_group(0)` on Unix,
//! `CREATE_NEW_PROCESS_GROUP` on Windows), concurrent `stdout`/`stderr` pipe
//! draining to prevent buffer deadlocks, process-tree escalation (`SIGTERM` ->
//! `SIGKILL` / `taskkill /F /T`), and per-process peak memory measurement:
//! - **macOS / BSD**: `libc::wait4` `ru_maxrss` (bytes on macOS, KiB on BSD).
//! - **Linux**: [`ProcfsSampler`] polling `/proc/<pid>/status` (`VmHWM`/`VmRSS`)
//!   with `/proc/<pid>/stat` `starttime` PID-reuse protection, falling back to
//!   `libc::wait4` `ru_maxrss * 1024` if the child exits before the first sample;
//!   plus `libc::prctl(PR_SET_CHILD_SUBREAPER, 1, ...)` via [`set_child_subreaper`].
//! - **Windows**: `windows_sys::Win32::System::ProcessStatus::GetProcessMemoryInfo`
//!   (`PeakWorkingSetSize` bytes) queried before closing the process handle.

use std::fs::File;
use std::io::Read;
use std::path::Path;
use std::process::{Child, ChildStdin, Command, ExitStatus};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use crate::util::env::PackageMetadata;
use crate::model::error::RunError;

/// Target operating system classification for normalizing `rusage::ru_maxrss` units.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TargetOs {
    /// Linux (`ru_maxrss` and `/proc/<pid>/status` report KiB).
    Linux,
    /// macOS (`ru_maxrss` reports bytes directly).
    MacOs,
    /// BSD variants (`ru_maxrss` reports KiB).
    Bsd,
    /// Windows (`PeakWorkingSetSize` reports bytes directly).
    Windows,
}

impl TargetOs {
    /// Return the [`TargetOs`] corresponding to the compile-time target platform.
    pub const fn current() -> Self {
        #[cfg(target_os = "macos")]
        {
            Self::MacOs
        }
        #[cfg(target_os = "linux")]
        {
            Self::Linux
        }
        #[cfg(target_os = "windows")]
        {
            Self::Windows
        }
        #[cfg(not(any(target_os = "macos", target_os = "linux", target_os = "windows")))]
        {
            Self::Bsd
        }
    }
}

/// Normalize a raw `ru_maxrss` (or Windows `PeakWorkingSetSize`) value into bytes.
pub fn normalize_memory_bytes(raw_rss: u64, os: TargetOs) -> u64 {
    match os {
        TargetOs::MacOs | TargetOs::Windows => raw_rss,
        TargetOs::Linux | TargetOs::Bsd => raw_rss.saturating_mul(1024),
    }
}

/// Mark the current process as a child subreaper (`PR_SET_CHILD_SUBREAPER`) on Linux.
///
/// Uses `libc::prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)` on Linux so orphaned
/// grandchildren are reparented to this process instead of PID 1. Returns `false`
/// on non-Linux platforms or if `prctl` fails.
pub fn set_child_subreaper() -> bool {
    #[cfg(target_os = "linux")]
    {
        // SAFETY: `prctl` with `PR_SET_CHILD_SUBREAPER` takes integer arguments only.
        unsafe { libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0 }
    }
    #[cfg(not(target_os = "linux"))]
    {
        false
    }
}

/// Parse `VmHWM:` (or fallback `VmRSS:`) in bytes from `/proc/<pid>/status` text.
pub fn parse_proc_status_vmhwm(content: &str) -> Option<u64> {
    for key in ["VmHWM:", "VmRSS:"] {
        if let Some(idx) = content.find(key) {
            let line = content[idx..].lines().next().unwrap_or("");
            let mut parts = line.split_whitespace();
            let _label = parts.next();
            if let Some(kib_str) = parts.next()
                && let Ok(kib) = kib_str.parse::<u64>()
            {
                return Some(kib.saturating_mul(1024));
            }
        }
    }
    None
}

/// Parse process `starttime` (field 22) from `/proc/<pid>/stat` text.
///
/// Uses `rfind(')')` so executable names containing spaces or parentheses in
/// `comm` do not shift subsequent field indices.
pub fn parse_proc_stat_starttime(content: &str) -> Option<u64> {
    let idx = content.rfind(')')?;
    let rest = &content[idx + 1..];
    let field = rest.split_whitespace().nth(19)?;
    field.parse::<u64>().ok()
}

/// Read up to `max_bytes` from a procfs file into a UTF-8 lossy string.
fn read_proc_text(path: &str, max_bytes: usize) -> Option<String> {
    let mut file = File::open(path).ok()?;
    let mut buf = vec![0u8; max_bytes];
    let n = file.read(&mut buf).ok()?;
    buf.truncate(n);
    Some(String::from_utf8_lossy(&buf).into_owned())
}

/// Read `/proc/<pid>/status` `VmHWM`/`VmRSS` in bytes.
fn read_procfs_vmhwm(pid: u32) -> Option<u64> {
    let max_bytes = PackageMetadata::get().limits.procfs_read_bytes;
    let content = read_proc_text(&format!("/proc/{pid}/status"), max_bytes)?;
    parse_proc_status_vmhwm(&content)
}

/// Process-wide termination flag set by `SIGINT` / `SIGTERM` handlers so waiting children abort and clean up.
static INTERRUPTED: AtomicBool = AtomicBool::new(false);

/// Mark the process as interrupted by `SIGINT` / `SIGTERM`.
pub fn request_interrupt() {
    INTERRUPTED.store(true, Ordering::Release);
}

/// Return `true` if a termination signal (`SIGINT` / `SIGTERM`) has been received.
pub fn is_interrupted() -> bool {
    INTERRUPTED.load(Ordering::Acquire)
}

/// Clear the global interrupt flag (primarily for tests).
pub fn clear_interrupt() {
    INTERRUPTED.store(false, Ordering::Release);
}

/// Read `/proc/<pid>/stat` `starttime` for PID-recycling detection.
fn read_procfs_starttime(pid: u32) -> Option<u64> {
    let content = read_proc_text(&format!("/proc/{pid}/stat"), 1024)?;
    parse_proc_stat_starttime(&content)
}

/// Background Linux `/proc/<pid>/status` peak RSS sampler with PID-reuse protection.
#[derive(Debug)]
pub struct ProcfsSampler {
    /// Target child PID.
    pid: u32,
    /// Polling interval.
    interval: Duration,
    /// Monotonically updated peak RSS in bytes.
    peak_bytes: Mutex<Option<u64>>,
    /// Signals background polling thread to exit.
    stop_flag: Arc<AtomicBool>,
    /// Background polling thread handle, if started.
    thread: Mutex<Option<JoinHandle<()>>>,
    /// Initial `/proc/<pid>/stat` `starttime` to guard against PID recycling.
    expected_starttime: Mutex<Option<u64>>,
}

impl ProcfsSampler {
    /// Create a new [`ProcfsSampler`] for `pid` polling every `interval`.
    pub fn new(pid: u32, interval: Duration) -> Arc<Self> {
        let initial_starttime = if cfg!(target_os = "linux") {
            read_procfs_starttime(pid)
        } else {
            None
        };
        Arc::new(Self {
            pid,
            interval,
            peak_bytes: Mutex::new(None),
            stop_flag: Arc::new(AtomicBool::new(false)),
            thread: Mutex::new(None),
            expected_starttime: Mutex::new(initial_starttime),
        })
    }

    /// Override the expected `starttime` (used by unit tests for PID-reuse verification).
    #[cfg(test)]
    fn set_expected_starttime(&self, starttime: Option<u64>) {
        *self.expected_starttime.lock().unwrap_or_else(|e| e.into_inner()) = starttime;
    }

    /// Return `true` if sampling has been halted due to stop or PID recycling.
    pub fn is_stopped(&self) -> bool {
        self.stop_flag.load(Ordering::Acquire)
    }

    /// Return the highest sampled peak RSS in bytes so far.
    pub fn peak_bytes(&self) -> Option<u64> {
        *self.peak_bytes.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Perform one sample pass against `/proc/<pid>/status` and `/proc/<pid>/stat`.
    pub fn sample(&self) -> Option<u64> {
        self.sample_with(read_procfs_vmhwm, read_procfs_starttime)
    }

    /// Perform one sample pass using injectable procfs readers (pure-testable on any OS).
    pub fn sample_with(
        &self,
        read_vmhwm: impl FnOnce(u32) -> Option<u64>,
        read_starttime: impl FnOnce(u32) -> Option<u64>,
    ) -> Option<u64> {
        let expected = *self
            .expected_starttime
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if let Some(expected_st) = expected
            && let Some(cur_st) = read_starttime(self.pid)
            && cur_st != expected_st
        {
            self.stop_flag.store(true, Ordering::Release);
            return self.peak_bytes();
        }

        let sampled = read_vmhwm(self.pid);
        let mut guard = self.peak_bytes.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(bytes) = sampled {
            *guard = Some(guard.map_or(bytes, |prev| prev.max(bytes)));
        }
        *guard
    }

    /// Start the background polling thread when running on Linux and `/proc/<pid>` exists.
    pub fn start(self: &Arc<Self>) {
        if !cfg!(target_os = "linux") {
            return;
        }
        if !Path::new(&format!("/proc/{}", self.pid)).exists() {
            return;
        }
        self.sample();
        let sampler = Arc::clone(self);
        let handle = thread::spawn(move || {
            while !sampler.stop_flag.load(Ordering::Acquire) {
                sampler.sample();
                thread::sleep(sampler.interval);
            }
        });
        *self.thread.lock().unwrap_or_else(|e| e.into_inner()) = Some(handle);
    }

    /// Stop background sampling, join the thread within `sampler_join_timeout_ms`, and return peak bytes.
    pub fn stop(&self) -> Option<u64> {
        self.stop_flag.store(true, Ordering::Release);
        let handle = self.thread.lock().unwrap_or_else(|e| e.into_inner()).take();
        if let Some(h) = handle {
            let join_limit = Duration::from_millis(PackageMetadata::get().limits.sampler_join_timeout_ms);
            let deadline = Instant::now() + join_limit;
            while !h.is_finished() && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(1));
            }
            if h.is_finished() {
                let _ = h.join();
            }
        }
        self.sample();
        self.peak_bytes()
    }
}

/// Captured output, exit status, peak RSS, and timeout flag from a completed [`MonitoredChild`].
#[derive(Debug, Clone)]
pub struct MonitoredOutput {
    /// Final process exit status.
    pub status: ExitStatus,
    /// Full captured standard output bytes.
    pub stdout: Vec<u8>,
    /// Full captured standard error bytes.
    pub stderr: Vec<u8>,
    /// Harvested per-process peak resident memory in bytes, if available.
    pub peak_memory_bytes: Option<u64>,
    /// `true` if execution exceeded the configured timeout and was terminated.
    pub timed_out: bool,
}

/// Cross-platform monitored child process isolating process groups and tracking peak RSS.
#[derive(Debug)]
pub struct MonitoredChild {
    /// Underlying `std::process::Child` handle.
    child: Child,
    /// Child process ID cached at spawn time.
    pid: u32,
    /// Cached exit status once reaped (`None` while running).
    exit_status: Option<ExitStatus>,
    /// Harvested peak memory in bytes once reaped (`None` while running).
    peak_memory_bytes: Option<u64>,
    /// Optional Linux procfs background sampler.
    procfs_sampler: Option<Arc<ProcfsSampler>>,
    /// Windows Job Object handle keeping all descendant processes in one killable tree.
    #[cfg(windows)]
    job: Option<WindowsJob>,
    /// Join handle for concurrent stdout pipe reader thread.
    stdout_handle: Option<JoinHandle<Vec<u8>>>,
    /// Join handle for concurrent stderr pipe reader thread.
    stderr_handle: Option<JoinHandle<Vec<u8>>>,
    /// Buffered stdout bytes after joining `stdout_handle`.
    stdout_buf: Vec<u8>,
    /// Buffered stderr bytes after joining `stderr_handle`.
    stderr_buf: Vec<u8>,
}

/// Spawn a pipe-draining reader thread for `pipe`.
fn spawn_pipe_reader<R: Read + Send + 'static>(mut pipe: R) -> JoinHandle<Vec<u8>> {
    thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = pipe.read_to_end(&mut buf);
        buf
    })
}

/// Default `SIGTERM` value used by cross-platform method signatures.
#[cfg(unix)]
const DEFAULT_SIGTERM: i32 = libc::SIGTERM;
/// Default `SIGTERM` placeholder on non-Unix platforms.
#[cfg(not(unix))]
const DEFAULT_SIGTERM: i32 = 15;

impl MonitoredChild {
    /// Spawn `cmd` in an isolated process group with concurrent pipe readers and optional Linux sampler.
    pub fn spawn(cmd: &mut Command) -> Result<Self, RunError> {
        Self::spawn_inner(cmd, true)
    }

    /// Spawn `cmd` in the caller's foreground process group so interactive TTY `stdin` (`Enter` / `Ctrl+D`) works without `SIGTTIN`.
    pub fn spawn_foreground(cmd: &mut Command) -> Result<Self, RunError> {
        Self::spawn_inner(cmd, false)
    }

    /// Shared spawn implementation optionally isolating the child into its own process group.
    fn spawn_inner(cmd: &mut Command, isolate_pgid: bool) -> Result<Self, RunError> {
        if isolate_pgid {
            #[cfg(unix)]
            {
                use std::os::unix::process::CommandExt;
                cmd.process_group(0);
            }
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                cmd.creation_flags(windows_sys::Win32::System::Threading::CREATE_NEW_PROCESS_GROUP);
            }
        }

        let mut child = cmd.spawn().map_err(RunError::Io)?;
        let pid = child.id();

        #[cfg(windows)]
        let job = WindowsJob::attach(&child);

        let procfs_sampler = if cfg!(target_os = "linux") {
            let interval =
                Duration::from_millis(PackageMetadata::get().limits.procfs_poll_interval_ms);
            let sampler = ProcfsSampler::new(pid, interval);
            sampler.start();
            Some(sampler)
        } else {
            None
        };

        let stdout_handle = child.stdout.take().map(spawn_pipe_reader);
        let stderr_handle = child.stderr.take().map(spawn_pipe_reader);

        Ok(Self {
            child,
            pid,
            exit_status: None,
            peak_memory_bytes: None,
            procfs_sampler,
            #[cfg(windows)]
            job,
            stdout_handle,
            stderr_handle,
            stdout_buf: Vec::new(),
            stderr_buf: Vec::new(),
        })
    }

    /// Return the OS process ID of this child.
    pub fn id(&self) -> u32 {
        self.pid
    }

    /// Take ownership of the child's standard input pipe, if configured as `Stdio::piped()`.
    pub fn take_stdin(&mut self) -> Option<ChildStdin> {
        self.child.stdin.take()
    }

    /// Return the harvested peak memory in bytes if the child has exited, else `None`.
    pub fn peak_memory_bytes(&self) -> Option<u64> {
        self.exit_status?;
        self.peak_memory_bytes
    }

    /// Join any active stdout/stderr reader threads and store their collected bytes.
    fn finish_pipes(&mut self) {
        if let Some(h) = self.stdout_handle.take()
            && let Ok(data) = h.join()
        {
            self.stdout_buf = data;
        }
        if let Some(h) = self.stderr_handle.take()
            && let Ok(data) = h.join()
        {
            self.stderr_buf = data;
        }
    }

    /// Return `(stdout, stderr)` collected by the background pipe reader threads.
    pub fn take_output(&mut self) -> (Vec<u8>, Vec<u8>) {
        self.finish_pipes();
        (
            std::mem::take(&mut self.stdout_buf),
            std::mem::take(&mut self.stderr_buf),
        )
    }

    /// Harvest peak memory on Windows via `GetProcessMemoryInfo` before closing the handle.
    #[cfg(windows)]
    fn query_windows_memory(&mut self) {
        use std::mem::size_of;
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::ProcessStatus::{
            GetProcessMemoryInfo, PROCESS_MEMORY_COUNTERS,
        };

        if self.peak_memory_bytes.is_some() {
            return;
        }
        let handle = self.child.as_raw_handle();
        let mut counters: PROCESS_MEMORY_COUNTERS = unsafe { std::mem::zeroed() };
        counters.cb = size_of::<PROCESS_MEMORY_COUNTERS>() as u32;
        // SAFETY: `handle` is a valid process HANDLE owned by `self.child` and `counters.cb` is set.
        let ok = unsafe {
            GetProcessMemoryInfo(handle as _, &mut counters, counters.cb)
        };
        if ok != 0 {
            self.peak_memory_bytes = Some(normalize_memory_bytes(
                counters.PeakWorkingSetSize as u64,
                TargetOs::Windows,
            ));
        }
    }

    /// Reap the Unix child exclusively via `libc::wait4` (never `Child::wait`/`try_wait`).
    #[cfg(unix)]
    fn reap_unix(&mut self, options: libc::c_int) -> Result<Option<ExitStatus>, RunError> {
        self.reap_unix_with(options, |pid, opts| {
            let mut raw_status: libc::c_int = 0;
            // SAFETY: `rusage` is POD and zero-initialized before passing to `libc::wait4`.
            let mut rusage: libc::rusage = unsafe { std::mem::zeroed() };
            // SAFETY: `pid` is our unwaited child PID; `raw_status` and `rusage` are valid pointers.
            let waited = unsafe {
                libc::wait4(pid as libc::pid_t, &mut raw_status, opts, &mut rusage)
            };
            if waited < 0 {
                let err = std::io::Error::last_os_error();
                Err(err.raw_os_error().unwrap_or(libc::EIO))
            } else {
                Ok((waited, raw_status, rusage.ru_maxrss.max(0) as u64))
            }
        })
    }

    /// Inner Unix `wait4` state machine accepting an injectable reaper for `ECHILD` testing.
    #[cfg(unix)]
    fn reap_unix_with<F>(&mut self, options: i32, mut wait4_fn: F) -> Result<Option<ExitStatus>, RunError>
    where
        F: FnMut(u32, i32) -> Result<(i32, i32, u64), i32>,
    {
        use std::os::unix::process::ExitStatusExt;

        if let Some(status) = self.exit_status {
            return Ok(Some(status));
        }

        loop {
            match wait4_fn(self.pid, options) {
                Ok((0, _, _)) => return Ok(None),
                Ok((_, raw_status, raw_maxrss)) => {
                    let status = ExitStatus::from_raw(raw_status);
                    self.exit_status = Some(status);

                    let sampler_bytes = self
                        .procfs_sampler
                        .as_ref()
                        .and_then(|s| s.stop());

                    if cfg!(target_os = "linux") {
                        let fallback = normalize_memory_bytes(raw_maxrss, TargetOs::Linux);
                        self.peak_memory_bytes = sampler_bytes.or(Some(fallback));
                    } else {
                        self.peak_memory_bytes =
                            Some(normalize_memory_bytes(raw_maxrss, TargetOs::current()));
                    }
                    return Ok(Some(status));
                }
                Err(errno) if errno == libc::EINTR && options == 0 => {
                    if is_interrupted() {
                        return Err(RunError::execution("Interrupted by signal"));
                    }
                    continue;
                }
                Err(errno) if errno == libc::ECHILD => {
                    if let Some(s) = &self.procfs_sampler {
                        let sampler_bytes = s.stop();
                        if self.peak_memory_bytes.is_none() {
                            self.peak_memory_bytes = sampler_bytes;
                        }
                    }
                    // SEC-R3-08: Never forge exit code 0 if ECHILD occurs before reaping.
                    let status = *self
                        .exit_status
                        .get_or_insert_with(|| ExitStatus::from_raw(1 << 8));
                    return Ok(Some(status));
                }
                Err(errno) => {
                    return Err(RunError::Io(std::io::Error::from_raw_os_error(errno)));
                }
            }
        }
    }

    /// Non-blocking poll for child termination.
    pub fn try_wait(&mut self) -> Result<Option<ExitStatus>, RunError> {
        #[cfg(unix)]
        {
            if let Some(s) = &self.procfs_sampler
                && self.exit_status.is_none()
            {
                s.sample();
            }
            self.reap_unix(libc::WNOHANG)
        }
        #[cfg(not(unix))]
        {
            if let Some(status) = self.exit_status {
                return Ok(Some(status));
            }
            if let Some(status) = self.child.try_wait().map_err(RunError::Io)? {
                self.exit_status = Some(status);
                #[cfg(windows)]
                self.query_windows_memory();
                return Ok(Some(status));
            }
            Ok(None)
        }
    }

    /// Block until the child exits, reap it, harvest memory, and join pipe reader threads.
    pub fn wait(&mut self) -> Result<ExitStatus, RunError> {
        #[cfg(unix)]
        let status = self
            .reap_unix(0)?
            .ok_or_else(|| RunError::execution("Blocking wait4 returned without status"))?;

        #[cfg(not(unix))]
        let status = {
            if let Some(existing) = self.exit_status {
                existing
            } else {
                let s = self.child.wait().map_err(RunError::Io)?;
                self.exit_status = Some(s);
                #[cfg(windows)]
                self.query_windows_memory();
                s
            }
        };

        self.finish_pipes();
        Ok(status)
    }

    /// Wait for the child to exit and return a [`MonitoredOutput`] without a timeout.
    pub fn wait_with_output(mut self) -> Result<MonitoredOutput, RunError> {
        let status = self.wait()?;
        let (stdout, stderr) = self.take_output();
        let peak_memory_bytes = self.peak_memory_bytes();
        Ok(MonitoredOutput {
            status,
            stdout,
            stderr,
            peak_memory_bytes,
            timed_out: false,
        })
    }

    /// Wait for child completion up to `timeout`, killing the process tree and harvesting memory on timeout.
    pub fn wait_with_timeout(
        &mut self,
        timeout: Option<Duration>,
        on_timeout: Option<&dyn Fn()>,
    ) -> Result<MonitoredOutput, RunError> {
        let Some(limit) = timeout else {
            let status = match self.wait() {
                Ok(s) => s,
                Err(err) if is_interrupted() => {
                    if let Some(cb) = on_timeout {
                        cb();
                    }
                    let grace =
                        Duration::from_millis(PackageMetadata::get().limits.kill_grace_period_ms);
                    self.kill_process_tree(DEFAULT_SIGTERM, grace);
                    return Err(err);
                }
                Err(err) => return Err(err),
            };
            let (stdout, stderr) = self.take_output();
            let peak_memory_bytes = self.peak_memory_bytes();
            return Ok(MonitoredOutput {
                status,
                stdout,
                stderr,
                peak_memory_bytes,
                timed_out: false,
            });
        };

        let deadline = Instant::now() + limit;
        loop {
            if is_interrupted() {
                if let Some(cb) = on_timeout {
                    cb();
                }
                let grace =
                    Duration::from_millis(PackageMetadata::get().limits.kill_grace_period_ms);
                self.kill_process_tree(DEFAULT_SIGTERM, grace);
                return Err(RunError::execution("Interrupted by signal"));
            }
            if let Some(status) = self.try_wait()? {
                let (stdout, stderr) = self.take_output();
                let peak_memory_bytes = self.peak_memory_bytes();
                return Ok(MonitoredOutput {
                    status,
                    stdout,
                    stderr,
                    peak_memory_bytes,
                    timed_out: false,
                });
            }
            let now = Instant::now();
            if now >= deadline {
                break;
            }
            let remaining = deadline.saturating_duration_since(now);
            thread::sleep(remaining.min(Duration::from_millis(5)));
        }

        if let Some(cb) = on_timeout {
            cb();
        }
        let grace = Duration::from_millis(PackageMetadata::get().limits.kill_grace_period_ms);
        self.kill_process_tree(DEFAULT_SIGTERM, grace);

        #[cfg(unix)]
        let status = self.exit_status.unwrap_or_else(|| {
            use std::os::unix::process::ExitStatusExt;
            let fallback = ExitStatus::from_raw(1 << 8);
            self.exit_status = Some(fallback);
            fallback
        });
        #[cfg(not(unix))]
        let status = match self.exit_status {
            Some(s) => s,
            None => self.wait()?,
        };

        let (stdout, stderr) = self.take_output();
        let peak_memory_bytes = self.peak_memory_bytes();
        Ok(MonitoredOutput {
            status,
            stdout,
            stderr,
            peak_memory_bytes,
            timed_out: true,
        })
    }

    /// Terminate the child and its entire process tree, escalating to `SIGKILL` after `grace_period`.
    pub fn kill_process_tree(&mut self, initial_sig: i32, grace_period: Duration) {
        #[cfg(windows)]
        {
            if self.try_wait().ok().flatten().is_some() {
                self.finish_pipes();
                return;
            }
            let _ = initial_sig;
            let _ = grace_period;
            if let Some(ref job) = self.job
                && job.terminate()
            {
                let _ = self.child.kill();
            } else {
                kill_windows_process_tree(self.pid, &mut self.child);
            }
            if self.exit_status.is_none()
                && let Ok(s) = self.child.wait()
            {
                self.exit_status = Some(s);
                self.query_windows_memory();
            }
            self.finish_pipes();
        }

        #[cfg(unix)]
        {
            let target_pid = self.pid as libc::pid_t;
            // Query process group before `try_wait` reaps `target_pid` (`getpgid` returns ESRCH once reaped).
            // SAFETY: `getpgid` reads kernel process group state without dereferencing pointers.
            let raw_pgid = unsafe { libc::getpgid(target_pid) };
            let pgid = if raw_pgid >= 0 {
                raw_pgid
            } else {
                // `spawn` places the child in its own process group (`process_group(0)`).
                target_pid
            };
            // SAFETY: `getpgrp` takes no arguments and cannot fail.
            let own_pgrp = unsafe { libc::getpgrp() };
            let is_own_pgrp = pgid == own_pgrp;
            let track_pgrp = !is_own_pgrp && pgid > 1;

            if self.try_wait().ok().flatten().is_some()
                && (!track_pgrp || !is_process_group_alive(pgid))
            {
                self.finish_pipes();
                return;
            }

            if raw_pgid < 0 && !track_pgrp {
                self.finish_pipes();
                return;
            }

            // SAFETY: Sends `initial_sig` to the isolated child process group or PID.
            unsafe {
                if !is_own_pgrp {
                    libc::killpg(pgid, initial_sig);
                } else if self.exit_status.is_none() {
                    libc::kill(target_pid, initial_sig);
                }
            }

            if !grace_period.is_zero() {
                let deadline = Instant::now() + grace_period;
                while Instant::now() < deadline {
                    let child_exited = self.try_wait().ok().flatten().is_some();
                    if child_exited && (!track_pgrp || !is_process_group_alive(pgid)) {
                        break;
                    }
                    thread::sleep(Duration::from_millis(10));
                }
            }

            let still_active =
                self.exit_status.is_none() || (track_pgrp && is_process_group_alive(pgid));
            if still_active {
                // SAFETY: Escalates to `SIGKILL` for the isolated child process group or PID.
                unsafe {
                    if !is_own_pgrp {
                        libc::killpg(pgid, libc::SIGKILL);
                    } else if self.exit_status.is_none() {
                        libc::kill(target_pid, libc::SIGKILL);
                    }
                }
                let wait_limit =
                    Duration::from_secs(PackageMetadata::get().limits.wait_after_kill_secs);
                let deadline = Instant::now() + wait_limit;
                while Instant::now() < deadline {
                    let child_exited = self.try_wait().ok().flatten().is_some();
                    if child_exited && (!track_pgrp || !is_process_group_alive(pgid)) {
                        break;
                    }
                    thread::sleep(Duration::from_millis(5));
                }
            }

            // Reap any orphaned descendants belonging strictly to this child's process group
            // (so concurrent test threads' children are never stolen).
            if track_pgrp && self.exit_status.is_some() {
                reap_process_group_zombies(pgid);
            }

            self.finish_pipes();
        }
    }
}

/// Reap any already-terminated zombie descendants belonging strictly to `pgid`.
#[cfg(unix)]
fn reap_process_group_zombies(pgid: libc::pid_t) {
    if pgid <= 1 {
        return;
    }
    loop {
        let mut st: libc::c_int = 0;
        // SAFETY: `-pgid` scopes reaping strictly to the isolated child process group.
        let w = unsafe { libc::waitpid(-pgid, &mut st, libc::WNOHANG) };
        if w <= 0 {
            break;
        }
    }
}

/// Reap zombie descendants in `pgid` and return `true` if any live process remains in `pgid`.
#[cfg(unix)]
fn is_process_group_alive(pgid: libc::pid_t) -> bool {
    reap_process_group_zombies(pgid);
    // SAFETY: Signal `0` probes process-group liveness without delivering a signal.
    let rc = unsafe { libc::killpg(pgid, 0) };
    if rc == 0 {
        return true;
    }
    std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

/// Terminate a Windows process tree using `taskkill /F /T /PID`, falling back to `child.kill()`.
#[cfg(windows)]
fn kill_windows_process_tree(pid: u32, child: &mut Child) {
    use std::process::Stdio;
    let timeout = Duration::from_secs(PackageMetadata::get().limits.taskkill_timeout_secs);
    let mut cmd = Command::new("taskkill");
    cmd.args(["/F", "/T", "/PID", &pid.to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let mut reaped_via_taskkill = false;
    if let Ok(mut tk) = cmd.spawn() {
        let deadline = Instant::now() + timeout;
        loop {
            match tk.try_wait() {
                Ok(Some(status)) => {
                    reaped_via_taskkill = status.success();
                    break;
                }
                Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(20)),
                _ => {
                    let _ = tk.kill();
                    let _ = tk.wait();
                    break;
                }
            }
        }
    }
    if !reaped_via_taskkill {
        let _ = child.kill();
    }
}

/// RAII wrapper around a Windows Job Object configured with `KILL_ON_JOB_CLOSE`.
#[cfg(windows)]
#[derive(Debug)]
struct WindowsJob(windows_sys::Win32::Foundation::HANDLE);

#[cfg(windows)]
// SAFETY: A Win32 Job Object HANDLE is an opaque kernel handle safe to send/drop across threads.
unsafe impl Send for WindowsJob {}
#[cfg(windows)]
unsafe impl Sync for WindowsJob {}

#[cfg(windows)]
impl WindowsJob {
    /// Create a job object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and assign `child` to it.
    fn attach(child: &Child) -> Option<Self> {
        use std::mem::size_of;
        use std::os::windows::io::AsRawHandle;
        use std::ptr;
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
            SetInformationJobObject,
        };

        // SAFETY: Null security/name creates an anonymous job object owned by this process.
        let job = unsafe { CreateJobObjectW(ptr::null(), ptr::null()) };
        if job.is_null() {
            return None;
        }

        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: `info` matches `JobObjectExtendedLimitInformation` and its byte size.
        let ok_set = unsafe {
            SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                (&raw const info).cast(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if ok_set == 0 {
            unsafe { CloseHandle(job) };
            return None;
        }

        let proc_handle = child.as_raw_handle() as windows_sys::Win32::Foundation::HANDLE;
        // SAFETY: `job` and `proc_handle` are valid kernel handles for this process lifetime.
        let ok_assign = unsafe { AssignProcessToJobObject(job, proc_handle) };
        if ok_assign == 0 {
            unsafe { CloseHandle(job) };
            return None;
        }

        Some(Self(job))
    }

    /// Terminate every process currently inside the job object.
    fn terminate(&self) -> bool {
        use windows_sys::Win32::System::JobObjects::TerminateJobObject;
        // SAFETY: `self.0` is a valid job object handle owned by `Self`.
        unsafe { TerminateJobObject(self.0, 1) != 0 }
    }
}

#[cfg(windows)]
impl Drop for WindowsJob {
    fn drop(&mut self) {
        // SAFETY: `self.0` is a non-null handle created by `CreateJobObjectW`.
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Stdio;
    use std::sync::atomic::AtomicUsize;

    /// Build a cross-platform `Command` invoking a short Python or shell inline snippet.
    ///
    /// Uses `python3` if present on Unix/macOS for exact MB allocation tests matching
    /// `test_memory_tracking.py`; falls back to `sh` where needed.
    fn python_cmd(code: &str) -> Command {
        let mut cmd = Command::new("python3");
        cmd.arg("-c").arg(code);
        cmd
    }

    /// Return `true` if `python3` is available in `PATH` for memory allocation tests.
    fn has_python3() -> bool {
        Command::new("python3")
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .is_ok_and(|s| s.success())
    }

    /// Port of `dev:tests/test_memory_tracking.py::test_boundary_unit_conversion_darwin_vs_linux`.
    #[test]
    fn test_normalize_memory_bytes_platforms() {
        assert_eq!(normalize_memory_bytes(50_000, TargetOs::Linux), 50_000 * 1024);
        assert_eq!(normalize_memory_bytes(50_000, TargetOs::Bsd), 50_000 * 1024);
        assert_eq!(normalize_memory_bytes(51_200_000, TargetOs::MacOs), 51_200_000);
        assert_eq!(normalize_memory_bytes(64 * 1024 * 1024, TargetOs::Windows), 64 * 1024 * 1024);
    }

    /// Verify pure `/proc/<pid>/status` (`VmHWM` and `VmRSS` fallback) and `/proc/<pid>/stat` parsing.
    #[test]
    fn test_parse_proc_status_and_stat_helpers() {
        let status_hwm = "Name:\tpython3\nState:\tR (running)\nVmPeak:\t123456 kB\nVmHWM:\t65536 kB\nVmRSS:\t32768 kB\n";
        assert_eq!(parse_proc_status_vmhwm(status_hwm), Some(65536 * 1024));

        let status_rss_only = "Name:\tapp\nVmRSS:\t1200 kB\n";
        assert_eq!(parse_proc_status_vmhwm(status_rss_only), Some(1200 * 1024));
        assert_eq!(parse_proc_status_vmhwm("Name:\tapp\n"), None);

        // `comm` containing spaces and `)` must not shift field 22 (`starttime`).
        let stat_line = "4210 (my (weird) prog) S 1 4210 4210 0 -1 4194304 10 0 0 0 1 0 0 0 20 0 1 0 987654321 1000 100";
        assert_eq!(parse_proc_stat_starttime(stat_line), Some(987_654_321));
        assert_eq!(parse_proc_stat_starttime("broken stat"), None);
    }

    /// Port of `dev:tests/test_memory_tracking.py::test_procfs_sampler_thread_safety_and_pid_recycling`.
    #[test]
    fn test_procfs_sampler_thread_safety_and_pid_recycling() {
        let sampler = ProcfsSampler::new(99_999, Duration::from_millis(1));
        sampler.set_expected_starttime(Some(12345));

        let mut handles = Vec::new();
        for _ in 0..10 {
            let s = Arc::clone(&sampler);
            handles.push(thread::spawn(move || {
                s.sample_with(|_| Some(50 * 1024 * 1024), |_| Some(12345))
            }));
        }
        for h in handles {
            h.join().expect("sampler thread join");
        }
        assert_eq!(sampler.peak_bytes(), Some(50 * 1024 * 1024));
        assert!(!sampler.is_stopped());

        // Different `starttime` indicates PID recycling -> stop and return existing peak.
        let res = sampler.sample_with(|_| Some(999 * 1024 * 1024), |_| Some(99_999));
        assert_eq!(res, Some(50 * 1024 * 1024));
        assert!(sampler.is_stopped());
    }

    /// Port of `dev:tests/test_memory_tracking.py::test_child_process_error_does_not_forge_zero_exit_code`.
    #[cfg(unix)]
    #[test]
    fn test_child_process_error_does_not_forge_zero_exit_code() {
        let mut cmd = Command::new("true");
        let mut proc = MonitoredChild::spawn(&mut cmd).expect("spawn true");
        // Reap cleanly first in background? Actually keep real child waited after test to avoid zombie:
        // Test `reap_unix_with` directly with simulated `ECHILD` on a separate MonitoredChild whose real
        // child is waited afterwards, or verify `reap_unix_with` when `exit_status` is `None`.
        let sim_status = proc
            .reap_unix_with(libc::WNOHANG, |_, _| Err(libc::ECHILD))
            .expect("ECHILD handled")
            .expect("returns status");
        assert!(!sim_status.success());
        assert_eq!(sim_status.code(), Some(1));

        // Clean up the real OS child so no zombie lingers.
        let mut raw_st: libc::c_int = 0;
        // SAFETY: `proc.pid` is an actual child spawned above.
        unsafe {
            libc::waitpid(proc.pid as libc::pid_t, &mut raw_st, 0);
        }
    }

    /// Port of `dev:tests/test_adversarial_memory_tracking.py::test_adversarial_api_memory_query_while_running`.
    #[test]
    fn test_api_memory_query_while_running_and_after_wait() {
        if !has_python3() {
            return;
        }
        let mut cmd = python_cmd("import time; time.sleep(0.25)");
        let mut proc = MonitoredChild::spawn(&mut cmd).expect("spawn python");
        assert_eq!(proc.peak_memory_bytes(), None);
        let status = proc.wait().expect("wait");
        assert!(status.success());
        assert!(proc.peak_memory_bytes().is_some_and(|b| b > 0));
    }

    /// Port of `dev:tests/test_adversarial_memory_tracking.py::test_adversarial_api_polling_then_wait`.
    #[test]
    fn test_api_polling_then_wait() {
        if !has_python3() {
            return;
        }
        let mut cmd = python_cmd("x = bytearray(b'\\x01') * (20 * 1024 * 1024); import time; time.sleep(0.08)");
        let mut proc = MonitoredChild::spawn(&mut cmd).expect("spawn python");
        while proc.try_wait().expect("try_wait").is_none() {
            thread::sleep(Duration::from_millis(10));
        }
        let status = proc.wait().expect("wait after poll");
        assert!(status.success());
        let mem = proc.peak_memory_bytes().expect("peak memory");
        assert!(mem >= 20 * 1024 * 1024, "expected >= 20MB, got {mem}");
    }

    /// Port of `dev:tests/test_memory_tracking.py::test_isolation_sequential_high_then_low_memory`.
    #[test]
    fn test_isolation_sequential_high_then_low_memory() {
        if !has_python3() {
            return;
        }
        let mut high_cmd = python_cmd("x = bytearray(b'\\x01') * (60 * 1024 * 1024)");
        let high_out = MonitoredChild::spawn(&mut high_cmd)
            .expect("spawn high")
            .wait_with_output()
            .expect("wait high");
        assert!(high_out.status.success());
        let mem_high = high_out.peak_memory_bytes.expect("high mem");
        assert!(mem_high >= 60 * 1024 * 1024, "got {mem_high}");

        let mut low_cmd = python_cmd("x = bytearray(b'\\x01') * (1 * 1024 * 1024)");
        let low_out = MonitoredChild::spawn(&mut low_cmd)
            .expect("spawn low")
            .wait_with_output()
            .expect("wait low");
        assert!(low_out.status.success());
        let mem_low = low_out.peak_memory_bytes.expect("low mem");
        assert!(
            mem_low < 35 * 1024 * 1024,
            "second run leaked/stacked memory: {mem_low} vs high {mem_high}"
        );
    }

    /// Port of `dev:tests/test_adversarial_memory_tracking.py` non-zero exit & signal termination tests.
    #[cfg(unix)]
    #[test]
    fn test_nonzero_exit_and_signals_preserve_memory() {
        if !has_python3() {
            return;
        }
        // Non-zero exit (42)
        let mut exit_cmd = python_cmd("import sys; x = bytearray(b'\\x01') * (20 * 1024 * 1024); sys.exit(42)");
        let out = MonitoredChild::spawn(&mut exit_cmd)
            .expect("spawn exit 42")
            .wait_with_output()
            .expect("wait");
        assert_eq!(out.status.code(), Some(42));
        assert!(out.peak_memory_bytes.is_some_and(|m| m >= 20 * 1024 * 1024));

        // SIGTERM and SIGKILL
        for sig in [libc::SIGTERM, libc::SIGKILL] {
            let mut sig_cmd = python_cmd("x = bytearray(b'\\x01') * (30 * 1024 * 1024); import time; time.sleep(10)");
            let mut proc = MonitoredChild::spawn(&mut sig_cmd).expect("spawn signal test");
            thread::sleep(Duration::from_millis(150));
            // SAFETY: Sends test signal to our still-running child process.
            unsafe {
                libc::kill(proc.id() as libc::pid_t, sig);
            }
            let status = proc.wait().expect("wait signaled child");
            use std::os::unix::process::ExitStatusExt;
            assert_eq!(status.signal(), Some(sig));
            let mem = proc.peak_memory_bytes().expect("memory on signal");
            assert!(mem >= 30 * 1024 * 1024, "sig {sig} mem={mem}");
        }
    }

    /// Verify `wait_with_timeout` kills child + grandchild process tree and invokes `on_timeout`.
    #[cfg(unix)]
    #[test]
    fn test_timeout_kills_grandchild_process_tree() {
        if !has_python3() {
            return;
        }
        let pid_file = std::env::temp_dir().join(format!(
            "run_proc_grandchild_{}.pid",
            std::process::id()
        ));
        let _ = std::fs::remove_file(&pid_file);

        let code = format!(
            "import subprocess, time, pathlib; \
             x = bytearray(b'\\x01') * (25 * 1024 * 1024); \
             p = subprocess.Popen(['sleep', '30']); \
             pathlib.Path({:?}).write_text(str(p.pid)); \
             time.sleep(30)",
            pid_file.to_string_lossy()
        );
        let mut cmd = python_cmd(&code);
        let mut proc = MonitoredChild::spawn(&mut cmd).expect("spawn tree");

        // Wait briefly until grandchild PID file is written.
        for _ in 0..40 {
            if pid_file.exists() {
                break;
            }
            thread::sleep(Duration::from_millis(25));
        }
        assert!(pid_file.exists(), "grandchild pid file was not written");
        let grandchild_pid: i32 = std::fs::read_to_string(&pid_file)
            .expect("read pid file")
            .trim()
            .parse()
            .expect("parse grandchild pid");

        let cb_hits = AtomicUsize::new(0);
        let out = proc
            .wait_with_timeout(Some(Duration::from_millis(120)), Some(&|| {
                cb_hits.fetch_add(1, Ordering::Relaxed);
            }))
            .expect("wait_with_timeout");

        assert!(out.timed_out);
        assert_eq!(cb_hits.load(Ordering::Relaxed), 1);
        assert!(out.peak_memory_bytes.is_some_and(|m| m >= 25 * 1024 * 1024));

        // Give OS a brief moment to complete process teardown, then confirm grandchild is gone.
        thread::sleep(Duration::from_millis(50));
        // SAFETY: `kill(pid, 0)` probes liveness without sending a signal.
        let alive = unsafe { libc::kill(grandchild_pid, 0) == 0 };
        let _ = std::fs::remove_file(&pid_file);
        assert!(!alive, "grandchild pid {grandchild_pid} survived kill_process_tree");
    }

    /// Port of `dev:tests/test_adversarial_memory_tracking.py::test_adversarial_api_large_io_stream_with_memory_tracking`.
    #[test]
    fn test_large_io_stream_no_deadlock() {
        if !has_python3() {
            return;
        }
        let mut cmd = python_cmd(
            "import sys; buf = bytearray(b'\\x01') * (15 * 1024 * 1024); sys.stdout.write('M' * (2 * 1024 * 1024)); sys.stderr.write('E' * (256 * 1024))",
        );
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
        let out = MonitoredChild::spawn(&mut cmd)
            .expect("spawn large io")
            .wait_with_output()
            .expect("wait large io");
        assert!(out.status.success());
        assert_eq!(out.stdout.len(), 2 * 1024 * 1024);
        assert_eq!(out.stderr.len(), 256 * 1024);
        assert!(out.peak_memory_bytes.is_some_and(|m| m >= 15 * 1024 * 1024));
    }

    /// Port of `dev:tests/test_adversarial_memory_tracking.py::test_adversarial_concurrent_monitored_popens_in_threads`.
    #[test]
    fn test_concurrent_monitored_children_in_threads() {
        if !has_python3() {
            return;
        }
        let sizes = [5usize, 40, 2, 60, 1, 50];
        let mut handles = Vec::new();
        for mb in sizes {
            handles.push(thread::spawn(move || {
                let mut cmd = python_cmd(&format!(
                    "x = bytearray(b'\\x01') * ({mb} * 1024 * 1024); import time; time.sleep(0.08)"
                ));
                let out = MonitoredChild::spawn(&mut cmd)
                    .expect("spawn worker")
                    .wait_with_output()
                    .expect("wait worker");
                (mb, out)
            }));
        }

        for h in handles {
            let (req_mb, out) = h.join().expect("worker join");
            assert!(out.status.success());
            let bytes = out.peak_memory_bytes.expect("worker mem");
            if req_mb >= 10 {
                assert!(
                    bytes >= (req_mb as u64) * 1024 * 1024,
                    "worker {req_mb}MB underreported: {bytes}"
                );
            } else {
                assert!(
                    bytes < 35 * 1024 * 1024,
                    "worker {req_mb}MB contaminated: {bytes}"
                );
            }
        }
    }

    /// Verify `set_child_subreaper` matches platform expectation (`true` on Linux, `false` elsewhere).
    #[test]
    fn test_set_child_subreaper_platform_behavior() {
        let enabled = set_child_subreaper();
        #[cfg(target_os = "linux")]
        assert!(enabled);
        #[cfg(not(target_os = "linux"))]
        assert!(!enabled);
    }

    /// Regression test: if direct child exits on `SIGTERM` while a grandchild ignores `SIGTERM`
    /// and holds stdout/stderr pipes open, `kill_process_tree` must escalate to `SIGKILL`
    /// and avoid deadlocking `finish_pipes()`.
    #[cfg(unix)]
    #[test]
    fn test_timeout_escalates_sigkill_for_sigterm_ignoring_grandchild() {
        if !has_python3() {
            return;
        }
        let pid_file = std::env::temp_dir().join(format!(
            "run_proc_sigign_grandchild_{}.pid",
            std::process::id()
        ));
        let tmp_pid_file = pid_file.with_extension("pid.tmp");
        let _ = std::fs::remove_file(&pid_file);
        let _ = std::fs::remove_file(&tmp_pid_file);

        // `sh` keeps default SIGTERM handling so it dies immediately on SIGTERM, while the
        // `python3` grandchild installs `SIG_IGN` for SIGTERM, inherits stdout/stderr pipes,
        // and atomically publishes its PID before sleeping.
        let py_snippet = format!(
            "import os, pathlib, signal, time; \
             signal.signal(signal.SIGTERM, signal.SIG_IGN); \
             tmp = pathlib.Path({:?}); \
             tmp.write_text(str(os.getpid())); \
             tmp.rename({:?}); \
             time.sleep(30)",
            tmp_pid_file.to_string_lossy(),
            pid_file.to_string_lossy()
        );
        let sh_script = format!("python3 -c '{}' & wait", py_snippet.replace('\'', "'\"'\"'"));
        let mut cmd = Command::new("sh");
        cmd.arg("-c")
            .arg(&sh_script)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let mut proc = MonitoredChild::spawn(&mut cmd).expect("spawn sh tree");
        for _ in 0..80 {
            if pid_file.exists() {
                break;
            }
            thread::sleep(Duration::from_millis(25));
        }
        assert!(pid_file.exists(), "grandchild pid file was not written");
        let grandchild_pid: i32 = std::fs::read_to_string(&pid_file)
            .expect("read pid file")
            .trim()
            .parse()
            .expect("parse grandchild pid");

        let started = Instant::now();
        let out = proc
            .wait_with_timeout(Some(Duration::from_millis(250)), None)
            .expect("wait_with_timeout");
        let elapsed = started.elapsed();

        assert!(out.timed_out);
        assert!(
            elapsed < Duration::from_secs(3),
            "wait_with_timeout took too long ({elapsed:?}), possible pipe deadlock"
        );

        // SAFETY: `kill(pid, 0)` probes liveness without sending a signal.
        let rc = unsafe { libc::kill(grandchild_pid, 0) };
        let errno = std::io::Error::last_os_error().raw_os_error();
        let _ = std::fs::remove_file(&pid_file);
        let _ = std::fs::remove_file(&tmp_pid_file);
        assert_eq!(rc, -1, "grandchild pid {grandchild_pid} survived kill_process_tree");
        assert_eq!(errno, Some(libc::ESRCH));
    }
}
