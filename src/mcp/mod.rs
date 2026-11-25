//! Model Context Protocol (MCP) stdio server (`--mcp`) for AI agent integrations.
//!
//! Exposes `run`'s compiler/runner, batch testcase runner, micro-benchmarker,
//! differential stress fuzzer, toolchain doctor, cache cleaner, and project
//! initializer as MCP tools backed by `run --json` subprocesses, plus a
//! `run://json-schema` resource describing the structured report format.

use std::path::{Component, Path, PathBuf};
use std::process::Stdio;

use rmcp::handler::server::{router::tool::ToolRouter, wrapper::Parameters};
use rmcp::model::{
    CallToolResult, ContentBlock, Implementation, ListResourcesResult, PaginatedRequestParams,
    ReadResourceRequestParams, ReadResourceResponse, ReadResourceResult, Resource,
    ResourceContents, ServerCapabilities, ServerConfig,
};
use rmcp::service::RequestContext;
use rmcp::{ErrorData as McpError, RoleServer, ServerHandler, ServiceExt, tool, tool_handler, tool_router};
use schemars::JsonSchema;
use serde::Deserialize;
use tokio::process::Command;

use crate::model::error::RunError;
use crate::model::report::JsonReport;

/// URI for the exposed `run --json` JSON Schema MCP resource.
pub const JSON_SCHEMA_RESOURCE_URI: &str = "run://json-schema";

/// Parameters for the `run_code` MCP tool.
#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct RunCodeParams {
    /// Target source file(s), project directory, or task/command names to execute.
    pub targets: Vec<String>,
    /// Optional inline standard input text forwarded via `--stdin-text`.
    pub stdin_text: Option<String>,
    /// Optional path to a file forwarded via `--stdin-file`.
    pub stdin_file: Option<String>,
    /// Optional expected output file path checked via `--expect`.
    pub expect: Option<String>,
    /// Extra arguments forwarded to the executed program after `--`.
    pub args: Option<Vec<String>>,
    /// Override compiler or interpreter executable (`--compiler`).
    pub compiler: Option<String>,
    /// Extra compiler/interpreter flags (`--flags`).
    pub flags: Option<String>,
    /// Config preset name(s) from `Run.toml` (`--preset`).
    pub preset: Option<Vec<String>>,
    /// Execution timeout in seconds (`--timeout`).
    pub timeout: Option<f64>,
    /// Execution memory limit (e.g. `256M`, `512MB`, `1G`) (`--mem-limit`).
    pub mem_limit: Option<String>,
    /// Working directory for the invocation (must stay inside `--mcp-root` if set).
    pub cwd: Option<String>,
    /// Disable persistent compilation cache (`--no-cache`).
    pub no_cache: Option<bool>,
    /// Compile only without executing (`--build-only`).
    pub build_only: Option<bool>,
    /// Directory for compiled artifacts (`--out-dir`).
    pub out_dir: Option<String>,
    /// Keep compiled binaries after execution (`--keep`).
    pub keep: Option<bool>,
    /// Execute inside configured container/native sandbox (`--sandbox`).
    pub sandbox: Option<bool>,
}

/// Parameters for the `run_tests` MCP tool.
#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct RunTestsParams {
    /// Target source file or project to test.
    pub target: String,
    /// Directory containing `.in`/`.out` test cases (`--test-dir`).
    pub test_dir: String,
    /// Optional glob pattern filtering testcase filenames (`--filter`).
    pub filter: Option<String>,
    /// Per-testcase execution timeout in seconds (`--timeout`).
    pub timeout: Option<f64>,
    /// Per-testcase memory limit (e.g. `256M`, `512MB`, `1G`) (`--mem-limit`).
    pub mem_limit: Option<String>,
    /// Parallel compilation/test threads (`--jobs`).
    pub jobs: Option<usize>,
    /// Override compiler or interpreter executable (`--compiler`).
    pub compiler: Option<String>,
    /// Extra compiler/interpreter flags (`--flags`).
    pub flags: Option<String>,
    /// Config preset name(s) from `Run.toml` (`--preset`).
    pub preset: Option<Vec<String>>,
    /// Working directory for the invocation (must stay inside `--mcp-root` if set).
    pub cwd: Option<String>,
    /// Disable persistent compilation cache (`--no-cache`).
    pub no_cache: Option<bool>,
}

/// Parameters for the `bench_code` MCP tool.
#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct BenchCodeParams {
    /// Target source file(s) or command to benchmark.
    pub targets: Vec<String>,
    /// Number of measured iterations (`--bench`, default 10).
    pub runs: Option<usize>,
    /// Number of warmup iterations (`--warmup`, default 2).
    pub warmup: Option<usize>,
    /// Extra arguments forwarded to the benchmarked program after `--`.
    pub args: Option<Vec<String>>,
    /// Optional inline standard input text (`--stdin-text`).
    pub stdin_text: Option<String>,
    /// Override compiler or interpreter executable (`--compiler`).
    pub compiler: Option<String>,
    /// Extra compiler/interpreter flags (`--flags`).
    pub flags: Option<String>,
    /// Config preset name(s) from `Run.toml` (`--preset`).
    pub preset: Option<Vec<String>>,
    /// Working directory for the invocation (must stay inside `--mcp-root` if set).
    pub cwd: Option<String>,
}

/// Parameters for the `stress_test` MCP tool.
#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct StressTestParams {
    /// Candidate solution file to fuzz against the brute-force oracle.
    pub target: String,
    /// Input generator script/binary (`<GEN>` in `--stress <GEN> <BRUTE>`).
    pub generator: String,
    /// Reference brute-force oracle script/binary (`<BRUTE>` in `--stress <GEN> <BRUTE>`).
    pub brute: String,
    /// Optional custom checker program (`--checker`).
    pub checker: Option<String>,
    /// Number of stress iterations (`--runs`, default 100).
    pub runs: Option<usize>,
    /// Starting RNG seed passed to the generator (`--seed`, default 1).
    pub seed: Option<u64>,
    /// Per-run execution timeout in seconds (`--timeout`).
    pub timeout: Option<f64>,
    /// Per-run memory limit (e.g. `256M`, `512MB`, `1G`) (`--mem-limit`).
    pub mem_limit: Option<String>,
    /// Working directory for the invocation (must stay inside `--mcp-root` if set).
    pub cwd: Option<String>,
}

/// Parameters for `doctor` and `clean_cache` MCP tools.
#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct CwdOnlyParams {
    /// Optional working directory (must stay inside `--mcp-root` if set).
    pub cwd: Option<String>,
}

/// Parameters for the `init_project` MCP tool.
#[derive(Debug, Clone, Deserialize, JsonSchema)]
pub struct InitProjectParams {
    /// Optional template name for `--new <NAME>` (`cpp`, `c`, `py`, `java`, `rust`, `go`, etc.).
    pub template: Option<String>,
    /// Optional file or project name to scaffold with `--new` (omit to run `--init` for `Run.toml`).
    pub name: Option<String>,
    /// Optional working directory (must stay inside `--mcp-root` if set).
    pub cwd: Option<String>,
}

/// MCP server exposing `run` capabilities over stdio using `rmcp`.
#[derive(Debug, Clone)]
pub struct RunMcpServer {
    /// Generated `rmcp` tool router (consumed by `#[tool_handler]`).
    #[expect(dead_code)]
    tool_router: ToolRouter<Self>,
    /// Optional canonicalized root directory confining `cwd` and relative path inputs.
    mcp_root: Option<PathBuf>,
}

impl RunMcpServer {
    /// Construct a new [`RunMcpServer`], canonicalizing `mcp_root` if provided.
    pub fn new(mcp_root: Option<&str>) -> Result<Self, RunError> {
        let canonical_root = match mcp_root {
            Some(dir) => {
                let p = Path::new(dir);
                if !p.is_dir() {
                    return Err(RunError::config(format!(
                        "--mcp-root directory does not exist: {dir}"
                    )));
                }
                let canon = std::fs::canonicalize(p).map_err(|err| {
                    RunError::config(format!("Failed to canonicalize --mcp-root '{dir}': {err}"))
                })?;
                Some(canon)
            }
            None => None,
        };
        Ok(Self {
            tool_router: Self::tool_router(),
            mcp_root: canonical_root,
        })
    }

    /// Resolve and validate `cwd` against `self.mcp_root` (if configured).
    fn resolve_cwd(&self, cwd: Option<&str>) -> Result<PathBuf, McpError> {
        let candidate = match (cwd, &self.mcp_root) {
            (Some(c), Some(root)) => {
                let p = Path::new(c);
                if p.is_absolute() {
                    p.to_path_buf()
                } else {
                    root.join(p)
                }
            }
            (Some(c), None) => PathBuf::from(c),
            (None, Some(root)) => root.clone(),
            (None, None) => std::env::current_dir().map_err(|err| {
                McpError::internal_error(format!("Failed to get current directory: {err}"), None)
            })?,
        };

        if !candidate.is_dir() {
            return Err(McpError::invalid_params(
                format!("Working directory does not exist: {}", candidate.display()),
                None,
            ));
        }

        let canon = std::fs::canonicalize(&candidate).map_err(|err| {
            McpError::invalid_params(
                format!(
                    "Failed to resolve working directory '{}': {err}",
                    candidate.display()
                ),
                None,
            )
        })?;

        if let Some(ref root) = self.mcp_root
            && !canon.starts_with(root)
        {
            return Err(McpError::invalid_params(
                format!(
                    "Working directory '{}' escapes --mcp-root '{}'",
                    canon.display(),
                    root.display()
                ),
                None,
            ));
        }

        Ok(canon)
    }

    /// Verify that a path argument does not escape `self.mcp_root` when `--mcp-root` is active.
    fn validate_path_arg(&self, cwd: &Path, raw: &str) -> Result<(), McpError> {
        let Some(ref root) = self.mcp_root else {
            return Ok(());
        };
        let candidate = if Path::new(raw).is_absolute() {
            PathBuf::from(raw)
        } else {
            cwd.join(raw)
        };
        let normalized = lexical_normalize(&candidate);
        if !normalized.starts_with(root) {
            return Err(McpError::invalid_params(
                format!(
                    "Path '{}' resolves outside --mcp-root '{}'",
                    raw,
                    root.display()
                ),
                None,
            ));
        }
        if candidate.exists()
            && let Ok(canon) = std::fs::canonicalize(&candidate)
            && !canon.starts_with(root)
        {
            return Err(McpError::invalid_params(
                format!(
                    "Path '{}' symlink resolves outside --mcp-root '{}'",
                    raw,
                    root.display()
                ),
                None,
            ));
        }
        Ok(())
    }

    /// Spawn `current_exe()` with `--json` + `args` inside the validated working directory.
    async fn invoke_run(
        &self,
        args: Vec<String>,
        cwd: &Path,
    ) -> Result<CallToolResult, McpError> {
        let exe = std::env::current_exe().map_err(|err| {
            McpError::internal_error(format!("Failed to locate run executable: {err}"), None)
        })?;

        let mut cmd = Command::new(exe);
        cmd.arg("--json")
            .args(&args)
            .current_dir(cwd)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let output = cmd.output().await.map_err(|err| {
            McpError::internal_error(format!("Failed to execute run subprocess: {err}"), None)
        })?;

        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();

        let payload = if !stdout.is_empty() {
            stdout
        } else if !stderr.is_empty() {
            stderr
        } else {
            serde_json::json!({
                "ok": output.status.success(),
                "exit_code": output.status.code().unwrap_or(1),
                "errors": ["No output produced by run subprocess"]
            })
            .to_string()
        };

        if output.status.success() {
            Ok(CallToolResult::success(vec![ContentBlock::text(payload)]))
        } else {
            Ok(CallToolResult::error(vec![ContentBlock::text(payload)]))
        }
    }
}

/// Lexically normalize `.` and `..` components without requiring the path to exist on disk.
fn lexical_normalize(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for comp in path.components() {
        match comp {
            Component::CurDir => {}
            Component::ParentDir => {
                if !out.pop() {
                    out.push(comp.as_os_str());
                }
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

/// Build CLI arguments for [`RunCodeParams`].
fn build_run_code_args(params: &RunCodeParams) -> Vec<String> {
    let mut args = Vec::new();
    if let Some(ref text) = params.stdin_text {
        args.push("--stdin-text".to_string());
        args.push(text.clone());
    }
    if let Some(ref file) = params.stdin_file {
        args.push("--stdin-file".to_string());
        args.push(file.clone());
    }
    if let Some(ref expect) = params.expect {
        args.push("--expect".to_string());
        args.push(expect.clone());
    }
    if let Some(ref compiler) = params.compiler {
        args.push("--compiler".to_string());
        args.push(compiler.clone());
    }
    if let Some(ref flags) = params.flags {
        args.push("--flags".to_string());
        args.push(flags.clone());
    }
    if let Some(ref presets) = params.preset {
        for p in presets {
            args.push("--preset".to_string());
            args.push(p.clone());
        }
    }
    if let Some(timeout) = params.timeout {
        args.push("--timeout".to_string());
        args.push(timeout.to_string());
    }
    if let Some(ref mem_limit) = params.mem_limit {
        args.push("--mem-limit".to_string());
        args.push(mem_limit.clone());
    }
    if params.no_cache.unwrap_or(false) {
        args.push("--no-cache".to_string());
    }
    if params.build_only.unwrap_or(false) {
        args.push("--build-only".to_string());
    }
    if let Some(ref out_dir) = params.out_dir {
        args.push("--out-dir".to_string());
        args.push(out_dir.clone());
    }
    if params.keep.unwrap_or(false) {
        args.push("--keep".to_string());
    }
    if params.sandbox.unwrap_or(false) {
        args.push("--sandbox".to_string());
    }
    args.extend(params.targets.iter().cloned());
    if let Some(ref extra) = params.args
        && !extra.is_empty()
    {
        args.push("--".to_string());
        args.extend(extra.iter().cloned());
    }
    args
}

/// Build CLI arguments for [`RunTestsParams`].
fn build_run_tests_args(params: &RunTestsParams) -> Vec<String> {
    let mut args = vec!["--test-dir".to_string(), params.test_dir.clone()];
    if let Some(ref filter) = params.filter {
        args.push("--filter".to_string());
        args.push(filter.clone());
    }
    if let Some(timeout) = params.timeout {
        args.push("--timeout".to_string());
        args.push(timeout.to_string());
    }
    if let Some(ref mem_limit) = params.mem_limit {
        args.push("--mem-limit".to_string());
        args.push(mem_limit.clone());
    }
    if let Some(jobs) = params.jobs {
        args.push("--jobs".to_string());
        args.push(jobs.to_string());
    }
    if let Some(ref compiler) = params.compiler {
        args.push("--compiler".to_string());
        args.push(compiler.clone());
    }
    if let Some(ref flags) = params.flags {
        args.push("--flags".to_string());
        args.push(flags.clone());
    }
    if let Some(ref presets) = params.preset {
        for p in presets {
            args.push("--preset".to_string());
            args.push(p.clone());
        }
    }
    if params.no_cache.unwrap_or(false) {
        args.push("--no-cache".to_string());
    }
    args.push(params.target.clone());
    args
}

/// Build CLI arguments for [`BenchCodeParams`].
fn build_bench_args(params: &BenchCodeParams) -> Vec<String> {
    let mut args = vec![
        "--bench".to_string(),
        params.runs.unwrap_or(10).to_string(),
    ];
    if let Some(warmup) = params.warmup {
        args.push("--warmup".to_string());
        args.push(warmup.to_string());
    }
    if let Some(ref text) = params.stdin_text {
        args.push("--stdin-text".to_string());
        args.push(text.clone());
    }
    if let Some(ref compiler) = params.compiler {
        args.push("--compiler".to_string());
        args.push(compiler.clone());
    }
    if let Some(ref flags) = params.flags {
        args.push("--flags".to_string());
        args.push(flags.clone());
    }
    if let Some(ref presets) = params.preset {
        for p in presets {
            args.push("--preset".to_string());
            args.push(p.clone());
        }
    }
    args.extend(params.targets.iter().cloned());
    if let Some(ref extra) = params.args
        && !extra.is_empty()
    {
        args.push("--".to_string());
        args.extend(extra.iter().cloned());
    }
    args
}

/// Build CLI arguments for [`StressTestParams`].
fn build_stress_args(params: &StressTestParams) -> Vec<String> {
    let mut args = vec![
        "--stress".to_string(),
        params.generator.clone(),
        params.brute.clone(),
    ];
    if let Some(ref checker) = params.checker {
        args.push("--checker".to_string());
        args.push(checker.clone());
    }
    if let Some(runs) = params.runs {
        args.push("--runs".to_string());
        args.push(runs.to_string());
    }
    if let Some(seed) = params.seed {
        args.push("--seed".to_string());
        args.push(seed.to_string());
    }
    if let Some(timeout) = params.timeout {
        args.push("--timeout".to_string());
        args.push(timeout.to_string());
    }
    if let Some(ref mem_limit) = params.mem_limit {
        args.push("--mem-limit".to_string());
        args.push(mem_limit.clone());
    }
    args.push(params.target.clone());
    args
}

#[tool_router]
impl RunMcpServer {
    /// Compile and run source file(s), projects, tasks, or PATH commands with structured JSON telemetry.
    #[tool(
        name = "run_code",
        description = "Compile and execute source file(s), projects, tasks, or PATH commands via `run --json`. Returns compile/run duration, peak RSS, exit code, stdout/stderr, and optional `--expect` diffs."
    )]
    async fn run_code(
        &self,
        Parameters(params): Parameters<RunCodeParams>,
    ) -> Result<CallToolResult, McpError> {
        let cwd = self.resolve_cwd(params.cwd.as_deref())?;
        for t in &params.targets {
            self.validate_path_arg(&cwd, t)?;
        }
        if let Some(ref f) = params.stdin_file {
            self.validate_path_arg(&cwd, f)?;
        }
        if let Some(ref e) = params.expect {
            self.validate_path_arg(&cwd, e)?;
        }
        if let Some(ref o) = params.out_dir {
            self.validate_path_arg(&cwd, o)?;
        }
        let args = build_run_code_args(&params);
        self.invoke_run(args, &cwd).await
    }

    /// Run a batch testcase suite (`--test-dir`) with optional `--filter` globbing.
    #[tool(
        name = "run_tests",
        description = "Run a batch `.in`/`.out` testcase directory (`--test-dir`) against a target program via `run --json`. Returns per-case pass/fail, timing, memory, and diffs."
    )]
    async fn run_tests(
        &self,
        Parameters(params): Parameters<RunTestsParams>,
    ) -> Result<CallToolResult, McpError> {
        let cwd = self.resolve_cwd(params.cwd.as_deref())?;
        self.validate_path_arg(&cwd, &params.target)?;
        self.validate_path_arg(&cwd, &params.test_dir)?;
        let args = build_run_tests_args(&params);
        self.invoke_run(args, &cwd).await
    }

    /// Micro-benchmark a program or command (`--bench`) with warmup and statistical summary.
    #[tool(
        name = "bench_code",
        description = "Micro-benchmark a program or command (`--bench`) via `run --json`. Returns min, median, mean, p95, max, and per-run durations."
    )]
    async fn bench_code(
        &self,
        Parameters(params): Parameters<BenchCodeParams>,
    ) -> Result<CallToolResult, McpError> {
        let cwd = self.resolve_cwd(params.cwd.as_deref())?;
        for t in &params.targets {
            self.validate_path_arg(&cwd, t)?;
        }
        let args = build_bench_args(&params);
        self.invoke_run(args, &cwd).await
    }

    /// Differential stress test a target against a generator and brute-force oracle (`--stress`).
    #[tool(
        name = "stress_test",
        description = "Run differential fuzzing (`--stress <GEN> <BRUTE>`) via `run --json`. Stops on first mismatch or crash and reports failing seed and artifact paths."
    )]
    async fn stress_test(
        &self,
        Parameters(params): Parameters<StressTestParams>,
    ) -> Result<CallToolResult, McpError> {
        let cwd = self.resolve_cwd(params.cwd.as_deref())?;
        self.validate_path_arg(&cwd, &params.target)?;
        self.validate_path_arg(&cwd, &params.generator)?;
        self.validate_path_arg(&cwd, &params.brute)?;
        if let Some(ref c) = params.checker {
            self.validate_path_arg(&cwd, c)?;
        }
        let args = build_stress_args(&params);
        self.invoke_run(args, &cwd).await
    }

    /// Inspect available compilers, interpreters, debuggers, and container engines (`--doctor`).
    #[tool(
        name = "doctor",
        description = "Check host compilers, interpreters, debuggers, and container engines via `run --doctor --json`."
    )]
    async fn doctor(
        &self,
        Parameters(params): Parameters<CwdOnlyParams>,
    ) -> Result<CallToolResult, McpError> {
        let cwd = self.resolve_cwd(params.cwd.as_deref())?;
        self.invoke_run(vec!["--doctor".to_string()], &cwd).await
    }

    /// Clear `run`'s persistent compilation cache (`--clean`).
    #[tool(
        name = "clean_cache",
        description = "Clear `run`'s persistent compilation and shebang cache via `run --clean --json`."
    )]
    async fn clean_cache(
        &self,
        Parameters(params): Parameters<CwdOnlyParams>,
    ) -> Result<CallToolResult, McpError> {
        let cwd = self.resolve_cwd(params.cwd.as_deref())?;
        self.invoke_run(vec!["--clean".to_string()], &cwd).await
    }

    /// Initialize `Run.toml` (`--init`) or scaffold a starter file/project (`--new`).
    #[tool(
        name = "init_project",
        description = "Initialize `Run.toml` (`--init`) or scaffold a starter file/project (`--new`) via `run --json`."
    )]
    async fn init_project(
        &self,
        Parameters(params): Parameters<InitProjectParams>,
    ) -> Result<CallToolResult, McpError> {
        let cwd = self.resolve_cwd(params.cwd.as_deref())?;
        let mut args = Vec::new();
        if let Some(ref name) = params.name {
            self.validate_path_arg(&cwd, name)?;
            args.push("--new".to_string());
            args.push(name.clone());
            if let Some(ref tmpl) = params.template {
                args.push("--template".to_string());
                args.push(tmpl.clone());
            }
        } else {
            args.push("--init".to_string());
        }
        self.invoke_run(args, &cwd).await
    }
}

#[tool_handler]
impl ServerHandler for RunMcpServer {
    fn get_info(&self) -> ServerConfig {
        let mut info = ServerConfig::default();
        info.capabilities = ServerCapabilities::builder()
            .enable_tools()
            .enable_resources()
            .build();
        info.server_info = Implementation::new("run", env!("CARGO_PKG_VERSION"));
        info.instructions = Some(
            "Polyglot compiler, runner, batch tester, micro-benchmarker, and differential fuzzer. \
             All tools return structured `run --json` output (see resource `run://json-schema`)."
                .to_string(),
        );
        info
    }

    async fn list_resources(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListResourcesResult, McpError> {
        let mut res = Resource::new(JSON_SCHEMA_RESOURCE_URI, "run-json-schema");
        res.description = Some("JSON Schema for `run --json` structured reports".to_string());
        res.mime_type = Some("application/json".to_string());
        Ok(ListResourcesResult {
            resources: vec![res],
            ..Default::default()
        })
    }

    async fn read_resource(
        &self,
        request: ReadResourceRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> Result<ReadResourceResponse, McpError> {
        if request.uri != JSON_SCHEMA_RESOURCE_URI {
            return Err(McpError::resource_not_found(
                format!("Unknown resource URI: {}", request.uri),
                None,
            ));
        }
        let schema_json = serde_json::to_string_pretty(&JsonReport::json_schema()).map_err(|err| {
            McpError::internal_error(format!("Failed to serialize JSON schema: {err}"), None)
        })?;
        let contents =
            ResourceContents::text(schema_json, JSON_SCHEMA_RESOURCE_URI).with_mime_type("application/json");
        Ok(ReadResourceResponse::Complete(ReadResourceResult::new(vec![
            contents,
        ])))
    }
}

/// Start the stdio MCP server and block until the client disconnects.
pub fn run_mcp_server(mcp_root: Option<&str>) -> Result<(), RunError> {
    let server = RunMcpServer::new(mcp_root)?;
    let rt = tokio::runtime::Runtime::new()
        .map_err(|err| RunError::Io(std::io::Error::other(err.to_string())))?;
    rt.block_on(async move {
        let service = server
            .serve(rmcp::transport::io::stdio())
            .await
            .map_err(|err| RunError::Io(std::io::Error::other(err.to_string())))?;
        service
            .waiting()
            .await
            .map_err(|err| RunError::Io(std::io::Error::other(err.to_string())))?;
        Ok(())
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn make_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos());
        let dir = std::env::temp_dir().join(format!(
            "run_mcp_test_{label}_{}_{}",
            std::process::id(),
            nanos
        ));
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    #[test]
    fn test_mcp_root_confinement_blocks_escape() {
        let base = make_temp_dir("root");
        let inside = base.join("sub");
        let outside = make_temp_dir("outside");
        fs::create_dir_all(&inside).unwrap();

        let srv = RunMcpServer::new(Some(&base.to_string_lossy())).expect("init server");
        let resolved_in = srv.resolve_cwd(Some("sub")).expect("inside sub");
        assert_eq!(resolved_in, fs::canonicalize(&inside).unwrap());

        assert!(srv.resolve_cwd(Some(&outside.to_string_lossy())).is_err());
        assert!(srv.resolve_cwd(Some("..")).is_err());

        assert!(srv.validate_path_arg(&resolved_in, "main.c").is_ok());
        assert!(srv.validate_path_arg(&resolved_in, "../../outside.c").is_err());

        let _ = fs::remove_dir_all(&base);
        let _ = fs::remove_dir_all(&outside);
    }

    #[test]
    fn test_build_tool_args_mapping() {
        let run_args = build_run_code_args(&RunCodeParams {
            targets: vec!["main.c".to_string()],
            stdin_text: Some("5 10\n".to_string()),
            stdin_file: None,
            expect: Some("out.txt".to_string()),
            args: Some(vec!["--flag".to_string()]),
            compiler: Some("clang".to_string()),
            flags: Some("-O2".to_string()),
            preset: Some(vec!["fast".to_string()]),
            timeout: Some(2.5),
            mem_limit: Some("256M".to_string()),
            cwd: None,
            no_cache: Some(true),
            build_only: Some(false),
            out_dir: None,
            keep: Some(false),
            sandbox: Some(false),
        });
        assert_eq!(
            run_args,
            vec![
                "--stdin-text",
                "5 10\n",
                "--expect",
                "out.txt",
                "--compiler",
                "clang",
                "--flags",
                "-O2",
                "--preset",
                "fast",
                "--timeout",
                "2.5",
                "--mem-limit",
                "256M",
                "--no-cache",
                "main.c",
                "--",
                "--flag",
            ]
        );

        let test_args = build_run_tests_args(&RunTestsParams {
            target: "sol.c".to_string(),
            test_dir: "cases".to_string(),
            filter: Some("edge_*".to_string()),
            timeout: Some(1.0),
            mem_limit: None,
            jobs: Some(4),
            compiler: None,
            flags: None,
            preset: None,
            cwd: None,
            no_cache: None,
        });
        assert_eq!(
            test_args,
            vec![
                "--test-dir",
                "cases",
                "--filter",
                "edge_*",
                "--timeout",
                "1",
                "--jobs",
                "4",
                "sol.c",
            ]
        );

        let bench_args = build_bench_args(&BenchCodeParams {
            targets: vec!["sol.c".to_string()],
            runs: Some(5),
            warmup: Some(1),
            args: None,
            stdin_text: None,
            compiler: None,
            flags: None,
            preset: None,
            cwd: None,
        });
        assert_eq!(
            bench_args,
            vec!["--bench", "5", "--warmup", "1", "sol.c"]
        );

        let stress_args = build_stress_args(&StressTestParams {
            target: "sol.c".to_string(),
            generator: "gen.py".to_string(),
            brute: "brute.py".to_string(),
            checker: Some("chk.py".to_string()),
            runs: Some(50),
            seed: Some(7),
            timeout: Some(2.0),
            mem_limit: None,
            cwd: None,
        });
        assert_eq!(
            stress_args,
            vec![
                "--stress",
                "gen.py",
                "brute.py",
                "--checker",
                "chk.py",
                "--runs",
                "50",
                "--seed",
                "7",
                "--timeout",
                "2",
                "sol.c",
            ]
        );
    }
}
