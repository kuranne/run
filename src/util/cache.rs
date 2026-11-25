//! Build artifact and source-dependency caching via composite SHA-256 checksums.
//!
//! Adapted from Python's `src/util/cache.py`, using `sha2::Sha256` for
//! streaming file and header-dependency hashing, `serde_json` for persisting
//! `cache.json` metadata (supporting both structured entries and legacy string
//! hashes), and `regex` for stripping C/C++ comments before scanning local
//! `#include "..."` dependencies. Cache paths follow R3 (`~/.cache/run/<hash>/`
//! or `$XDG_CACHE_HOME/run/<hash>/` on Unix, `%LOCALAPPDATA%\run\<hash>\` on
//! Windows) using constants from [`crate::util::env::PackageMetadata`].

use std::collections::{BTreeMap, HashSet};
use std::ffi::OsStr;
use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use regex::Regex;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::util::env::PackageMetadata;
use crate::model::error::RunError;
use crate::util::output::Printer;

/// Cached compilation entry stored in `cache.json`.
///
/// Uses `#[serde(untagged)]` via `serde` so both modern structured entries and
/// legacy raw hash string entries deserialize seamlessly.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum CacheEntry {
    /// Modern entry storing composite source hash and optional output binary hash/path.
    Detailed {
        /// Composite SHA-256 hex digest of the source file and its local includes.
        source_hash: String,
        /// SHA-256 hex digest of the compiled binary or object file, if present.
        binary_hash: Option<String>,
        /// Absolute path of the compiled binary or object file, if recorded.
        binary_path: Option<String>,
    },
    /// Legacy cache format storing only the composite source hash string.
    Legacy(String),
}

/// Manages per-project build caching under `~/.cache/run/<project_hash>/`.
///
/// Maintains `objs/`, `bin/`, `src/`, and `cache.json` using a `Mutex`-guarded
/// [`BTreeMap`] so concurrent updates in parallel builds remain serialized and
/// deterministic.
#[derive(Debug)]
pub struct CacheManager {
    /// Root cache bucket directory (`<base_cache>/run/<hash[:16]>`).
    pub cache_dir: PathBuf,
    /// Directory for cached object files (`objs`).
    pub objs_dir: PathBuf,
    /// Directory for cached compiled binaries (`bin`).
    pub bin_dir: PathBuf,
    /// Directory for sanitized/preprocessed source files (`src`).
    pub src_dir: PathBuf,
    /// Path to the JSON metadata index (`cache.json`).
    pub cache_file: PathBuf,
    /// Mutex-protected in-memory mirror of `cache.json`.
    cache_data: Mutex<BTreeMap<String, CacheEntry>>,
}

/// Cached regex stripping C/C++ `//...` and `/*...*/` comments.
static COMMENT_RE: OnceLock<Regex> = OnceLock::new();

/// Cached regex matching local `#include "..."` directives.
static INCLUDE_RE: OnceLock<Regex> = OnceLock::new();

/// Return the compiled comment-stripping regex (`//` to EOL/EOF and `/* ... */`).
fn comment_regex() -> &'static Regex {
    COMMENT_RE.get_or_init(|| {
        Regex::new(r"(?m)//[^\n]*$|(?s)/\*.*?\*/").expect("valid C/C++ comment regex")
    })
}

/// Return the compiled `#include "..."` matching regex.
fn include_regex() -> &'static Regex {
    INCLUDE_RE
        .get_or_init(|| Regex::new(r#"#\s*include\s*"([^"]+)""#).expect("valid C/C++ include regex"))
}

/// Compute lowercase hex SHA-256 digest of arbitrary bytes using `sha2::Sha256`.
fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

/// Compute truncated hex SHA-256 prefix of a lexically normalized absolute path.
fn path_hash_prefix(path: &Path, prefix_len: usize) -> String {
    let abs = std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf());
    let normalized: PathBuf = abs.components().collect();
    let digest = sha256_hex(normalized.to_string_lossy().as_bytes());
    digest.chars().take(prefix_len).collect()
}

/// Remove a file, retrying briefly on Windows when Defender/OS holds a transient lock on a cached `.exe`.
fn remove_file_retry(path: &Path) {
    #[cfg(windows)]
    {
        for attempt in 0..10 {
            match fs::remove_file(path) {
                Ok(()) => return,
                Err(err) if err.kind() == std::io::ErrorKind::PermissionDenied && attempt < 9 => {
                    std::thread::sleep(std::time::Duration::from_millis(25));
                }
                Err(_) => return,
            }
        }
    }
    #[cfg(not(windows))]
    {
        let _ = fs::remove_file(path);
    }
}

/// Resolve the OS-specific base cache directory from explicit env values (pure helper).
///
/// Resolves to `$XDG_CACHE_HOME/run` or `$HOME/.cache/run` on Unix/macOS, and
/// `%LOCALAPPDATA%\run` or `%USERPROFILE%\AppData\Local\run` on Windows, using
/// [`PackageMetadata::get`] (`cache_dir_name`).
pub fn cache_base_dir_from(
    primary_override: Option<&OsStr>,
    home_dir: Option<&OsStr>,
) -> Option<PathBuf> {
    let dir_name = &PackageMetadata::get().cache_dir_name;
    if let Some(val) = primary_override
        && !val.is_empty()
    {
        return Some(PathBuf::from(val).join(dir_name));
    }
    let home = home_dir.filter(|h| !h.is_empty())?;
    #[cfg(windows)]
    {
        Some(
            PathBuf::from(home)
                .join("AppData")
                .join("Local")
                .join(dir_name),
        )
    }
    #[cfg(not(windows))]
    {
        Some(PathBuf::from(home).join(".cache").join(dir_name))
    }
}

/// Resolve the OS-specific base cache directory from the active process environment.
fn default_cache_base_dir() -> Result<PathBuf, RunError> {
    #[cfg(windows)]
    let resolved = cache_base_dir_from(
        std::env::var_os("LOCALAPPDATA").as_deref(),
        std::env::var_os("USERPROFILE").as_deref(),
    );
    #[cfg(not(windows))]
    let resolved = cache_base_dir_from(
        std::env::var_os("XDG_CACHE_HOME").as_deref(),
        std::env::var_os("HOME").as_deref(),
    );

    resolved.ok_or_else(|| {
        RunError::config("Unable to determine user cache directory from environment")
    })
}

/// Return `true` if `path` has an extension in `exts` (case-insensitive).
fn has_extension(path: &Path, exts: &[String]) -> bool {
    let Some(ext) = path.extension().and_then(|e| e.to_str()) else {
        return false;
    };
    let dotted = format!(".{}", ext.to_ascii_lowercase());
    exts.iter().any(|candidate| candidate == &dotted)
}

/// Check if an extensionless or non-standard file starts with a C/C++ `run` shebang or `#include`.
fn has_c_shebang(path: &Path, head_limit: usize) -> bool {
    let Ok(mut file) = File::open(path) else {
        return false;
    };
    let mut buf = vec![0u8; head_limit];
    let Ok(n) = file.read(&mut buf) else {
        return false;
    };
    buf.truncate(n);
    let head = String::from_utf8_lossy(&buf);
    if !head.starts_with("#!") {
        return false;
    }
    let first_line = head.split('\n').next().unwrap_or("");
    first_line.contains("run") || head.contains("#include")
}

impl CacheManager {
    /// Initialize a [`CacheManager`] for `project_root` using the OS user cache directory.
    pub fn new(project_root: &Path) -> Result<Self, RunError> {
        let base_dir = default_cache_base_dir()?;
        Self::with_cache_base(project_root, &base_dir)
    }

    /// Initialize a [`CacheManager`] using an explicit base cache directory (e.g. `<tmp>/run`).
    ///
    /// Allows unit tests to isolate cache directories without mutating process-global
    /// environment variables.
    pub fn with_cache_base(project_root: &Path, base_cache_dir: &Path) -> Result<Self, RunError> {
        let meta = PackageMetadata::get();
        let project_hash = path_hash_prefix(project_root, meta.cache.hash_prefix_len);
        let cache_dir = base_cache_dir.join(project_hash);
        let objs_dir = cache_dir.join(&meta.cache.objs_dir);
        let bin_dir = cache_dir.join(&meta.cache.bin_dir);
        let src_dir = cache_dir.join(&meta.cache.src_dir);
        let cache_file = cache_dir.join(&meta.cache.metadata_file);

        let cache_data = Self::load_cache_from_disk(&cache_file);
        Ok(Self {
            cache_dir,
            objs_dir,
            bin_dir,
            src_dir,
            cache_file,
            cache_data: Mutex::new(cache_data),
        })
    }

    /// Load `cache.json` via `serde_json`, logging a warning and starting fresh on error.
    fn load_cache_from_disk(cache_file: &Path) -> BTreeMap<String, CacheEntry> {
        if !cache_file.exists() {
            return BTreeMap::new();
        }
        match fs::read_to_string(cache_file) {
            Ok(content) => match serde_json::from_str::<BTreeMap<String, CacheEntry>>(&content) {
                Ok(map) => map,
                Err(_) => {
                    Printer::global().warning("Failed to load cache, starting fresh.");
                    BTreeMap::new()
                }
            },
            Err(_) => {
                Printer::global().warning("Failed to load cache, starting fresh.");
                BTreeMap::new()
            }
        }
    }

    /// Persist `data` to `cache.json` while holding the cache mutex.
    ///
    /// Removes `cache.json` and an empty `cache_dir` when `data` is empty.
    fn save_cache_locked(&self, data: &BTreeMap<String, CacheEntry>) {
        if data.is_empty() {
            if self.cache_file.exists() {
                let _ = fs::remove_file(&self.cache_file);
            }
            if self.cache_dir.exists()
                && fs::read_dir(&self.cache_dir).is_ok_and(|mut it| it.next().is_none())
            {
                let _ = fs::remove_dir(&self.cache_dir);
            }
            return;
        }

        if !self.cache_dir.exists() && fs::create_dir_all(&self.cache_dir).is_err() {
            return;
        }

        if let Ok(json) = serde_json::to_string_pretty(data) {
            let _ = fs::write(&self.cache_file, json);
        }
    }

    /// Return a unique object-file path in `objs/` for `source_path`.
    pub fn get_object_path(&self, source_path: &Path) -> PathBuf {
        let meta = PackageMetadata::get();
        let path_hash = path_hash_prefix(source_path, meta.cache.hash_prefix_len);
        if !self.objs_dir.exists() {
            let _ = fs::create_dir_all(&self.objs_dir);
        }
        let fname = source_path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("source");
        self.objs_dir.join(format!("{path_hash}_{fname}.o"))
    }

    /// Return a unique compiled-binary path in `bin/` for `source_path`.
    pub fn get_cached_binary_path(&self, source_path: &Path) -> PathBuf {
        let meta = PackageMetadata::get();
        let path_hash = path_hash_prefix(source_path, meta.cache.hash_prefix_len);
        if !self.bin_dir.exists() {
            let _ = fs::create_dir_all(&self.bin_dir);
        }
        let stem = source_path
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("app");
        let ext = if cfg!(windows) { ".exe" } else { ".out" };
        self.bin_dir.join(format!("{path_hash}_{stem}{ext}"))
    }

    /// Return a unique preprocessed source path in `src/` for `source_path`.
    pub fn get_sanitized_source_path(&self, source_path: &Path, ext: &str) -> PathBuf {
        let meta = PackageMetadata::get();
        let path_hash = path_hash_prefix(source_path, meta.cache.hash_prefix_len);
        if !self.src_dir.exists() {
            let _ = fs::create_dir_all(&self.src_dir);
        }
        let stem = source_path
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("source");
        let clean_ext = if ext.starts_with('.') {
            ext.to_string()
        } else {
            format!(".{ext}")
        };
        self.src_dir.join(format!("{path_hash}_{stem}{clean_ext}"))
    }

    /// Calculate the lowercase hex SHA-256 digest of a single file, or `""` if missing/unreadable.
    pub fn get_file_hash(&self, file_path: &Path) -> String {
        if !file_path.exists() {
            return String::new();
        }
        let Ok(mut file) = File::open(file_path) else {
            return String::new();
        };
        let chunk_size = PackageMetadata::get().cache.hash_chunk_bytes.max(1);
        let mut buf = vec![0u8; chunk_size];
        let mut hasher = Sha256::new();

        loop {
            match file.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => hasher.update(&buf[..n]),
                Err(_) => return String::new(),
            }
        }
        format!("{:x}", hasher.finalize())
    }

    /// Recursively extract local `#include "..."` header dependencies for C/C++ files.
    pub fn get_c_includes(&self, file_path: &Path) -> Vec<PathBuf> {
        let mut visited = HashSet::new();
        self.collect_c_includes(file_path, &mut visited)
    }

    /// Recursive worker for [`Self::get_c_includes`] guarded by canonical `visited` paths.
    fn collect_c_includes(&self, file_path: &Path, visited: &mut HashSet<PathBuf>) -> Vec<PathBuf> {
        let mut includes = Vec::new();
        if !file_path.exists() {
            return includes;
        }
        let canonical = fs::canonicalize(file_path)
            .unwrap_or_else(|_| std::path::absolute(file_path).unwrap_or_else(|_| file_path.to_path_buf()));
        if !visited.insert(canonical) {
            return includes;
        }

        let meta = PackageMetadata::get();
        let is_c = has_extension(file_path, &meta.extensions.c)
            || has_extension(file_path, &meta.extensions.cpp)
            || has_extension(file_path, &meta.extensions.c_headers)
            || (file_path.is_file() && has_c_shebang(file_path, meta.cache.shebang_head_bytes));
        if !is_c {
            return includes;
        }

        let Ok(raw_bytes) = fs::read(file_path) else {
            return includes;
        };
        let content = String::from_utf8_lossy(&raw_bytes);
        let clean = comment_regex().replace_all(&content, "");

        let parent = file_path.parent().unwrap_or_else(|| Path::new("."));
        for cap in include_regex().captures_iter(&clean) {
            let inc_rel = &cap[1];
            let candidate = parent.join(inc_rel);
            if let Ok(inc_path) = fs::canonicalize(&candidate)
                && !visited.contains(&inc_path)
            {
                includes.push(inc_path.clone());
                includes.extend(self.collect_c_includes(&inc_path, visited));
            }
        }

        includes
    }

    /// Calculate the composite SHA-256 hash of `file_path` and its sorted local header dependencies.
    pub fn get_composite_hash(&self, file_path: &Path) -> String {
        if !file_path.exists() {
            return String::new();
        }
        let mut hasher = Sha256::new();
        let self_hash = self.get_file_hash(file_path);
        hasher.update(self_hash.as_bytes());

        let meta = PackageMetadata::get();
        let is_c = has_extension(file_path, &meta.extensions.c)
            || has_extension(file_path, &meta.extensions.cpp)
            || (file_path.is_file() && has_c_shebang(file_path, meta.cache.shebang_head_bytes));

        if is_c {
            let mut includes = self.get_c_includes(file_path);
            includes.sort_by(|a, b| a.to_string_lossy().cmp(&b.to_string_lossy()));
            for inc in includes {
                let inc_hash = self.get_file_hash(&inc);
                let name = inc.file_name().and_then(|n| n.to_str()).unwrap_or("");
                hasher.update(format!("{name}:{inc_hash}").as_bytes());
            }
        }

        format!("{:x}", hasher.finalize())
    }

    /// Return `true` if `file_path`, its headers, or `output_path` changed or are uncached.
    pub fn is_changed(&self, file_path: &Path, output_path: Option<&Path>) -> bool {
        let key = std::path::absolute(file_path)
            .unwrap_or_else(|_| file_path.to_path_buf())
            .to_string_lossy()
            .into_owned();
        let current_source_hash = self.get_composite_hash(file_path);

        let entry = {
            let guard = self.cache_data.lock().unwrap_or_else(|e| e.into_inner());
            guard.get(&key).cloned()
        };

        let Some(entry) = entry else {
            return true;
        };

        match entry {
            CacheEntry::Detailed {
                source_hash,
                binary_hash,
                ..
            } => {
                if source_hash != current_source_hash {
                    return true;
                }
                if let Some(out) = output_path {
                    if !out.exists() {
                        return true;
                    }
                    let current_bin_hash = self.get_file_hash(out);
                    if binary_hash.as_deref() != Some(current_bin_hash.as_str()) {
                        return true;
                    }
                }
                false
            }
            CacheEntry::Legacy(old_hash) => {
                if old_hash != current_source_hash {
                    return true;
                }
                if let Some(out) = output_path
                    && !out.exists()
                {
                    return true;
                }
                false
            }
        }
    }

    /// Update the cache entry for `file_path` (and optional `output_path`) and persist `cache.json`.
    pub fn update_cache(&self, file_path: &Path, output_path: Option<&Path>) {
        let key = std::path::absolute(file_path)
            .unwrap_or_else(|_| file_path.to_path_buf())
            .to_string_lossy()
            .into_owned();
        let comp_hash = self.get_composite_hash(file_path);

        let (bin_hash, bin_path) = match output_path {
            Some(out) if out.exists() => (
                Some(self.get_file_hash(out)),
                Some(
                    std::path::absolute(out)
                        .unwrap_or_else(|_| out.to_path_buf())
                        .to_string_lossy()
                        .into_owned(),
                ),
            ),
            Some(out) => (
                None,
                Some(
                    std::path::absolute(out)
                        .unwrap_or_else(|_| out.to_path_buf())
                        .to_string_lossy()
                        .into_owned(),
                ),
            ),
            None => (None, None),
        };

        let entry = CacheEntry::Detailed {
            source_hash: comp_hash,
            binary_hash: bin_hash,
            binary_path: bin_path,
        };

        let mut guard = self.cache_data.lock().unwrap_or_else(|e| e.into_inner());
        guard.insert(key, entry);
        self.save_cache_locked(&guard);
    }

    /// Clear all in-memory cache entries and remove cached object/binary/source files, symlinks, and directories.
    pub fn clear(&self) {
        let mut guard = self.cache_data.lock().unwrap_or_else(|e| e.into_inner());
        guard.clear();

        for dir in [&self.objs_dir, &self.bin_dir, &self.src_dir] {
            if let Ok(dir_meta) = fs::symlink_metadata(dir) {
                if dir_meta.file_type().is_dir() {
                    if let Ok(entries) = fs::read_dir(dir) {
                        for entry in entries.flatten() {
                            let path = entry.path();
                            if let Ok(meta) = fs::symlink_metadata(&path) {
                                if meta.file_type().is_dir() {
                                    let _ = fs::remove_dir_all(&path);
                                } else {
                                    remove_file_retry(&path);
                                }
                            }
                        }
                    }
                    let _ = fs::remove_dir(dir);
                } else {
                    remove_file_retry(dir);
                }
            }
        }

        if fs::symlink_metadata(&self.cache_file).is_ok() {
            let _ = fs::remove_file(&self.cache_file);
        }

        if self.cache_dir.exists()
            && fs::read_dir(&self.cache_dir).is_ok_and(|mut it| it.next().is_none())
        {
            let _ = fs::remove_dir(&self.cache_dir);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::thread;
    use std::time::{SystemTime, UNIX_EPOCH};

    /// Create an isolated temporary directory for cache tests.
    fn make_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock went backwards")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "run_cache_test_{label}_{}_{}",
            std::process::id(),
            nanos
        ));
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    /// Build a `CacheManager` rooted at `tmp_dir` with an isolated cache base.
    fn make_manager(tmp_dir: &Path) -> CacheManager {
        let cache_base = tmp_dir.join("cache_home").join("run");
        CacheManager::with_cache_base(tmp_dir, &cache_base).expect("create cache manager")
    }

    /// Port of `dev:tests/test_cache.py::test_cache_init_and_hash`.
    #[test]
    fn test_cache_init_and_hash() {
        let tmp = make_temp_dir("init_hash");
        let manager = make_manager(&tmp);
        let test_file = tmp.join("test.c");
        fs::write(&test_file, "int main() {}").expect("write test.c");

        assert!(manager.is_changed(&test_file, None));
        manager.update_cache(&test_file, None);
        assert!(!manager.is_changed(&test_file, None));

        fs::write(&test_file, "int main() { return 0; }").expect("modify test.c");
        assert!(manager.is_changed(&test_file, None));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_cache.py::test_cache_clear`.
    #[test]
    fn test_cache_clear() {
        let tmp = make_temp_dir("clear");
        let manager = make_manager(&tmp);
        let test_file = tmp.join("test.c");
        fs::write(&test_file, "code").expect("write test.c");

        manager.update_cache(&test_file, None);
        assert!(!manager.is_changed(&test_file, None));

        manager.clear();
        assert!(manager.is_changed(&test_file, None));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_cache.py::test_cache_header_dependency`.
    #[test]
    fn test_cache_header_dependency() {
        let tmp = make_temp_dir("header_dep");
        let manager = make_manager(&tmp);
        let header = tmp.join("helper.h");
        fs::write(&header, "int get_val() { return 1; }").expect("write helper.h");

        let source = tmp.join("main.c");
        fs::write(&source, "#include \"helper.h\"\nint main() { return get_val(); }")
            .expect("write main.c");

        assert!(manager.is_changed(&source, None));
        manager.update_cache(&source, None);
        assert!(!manager.is_changed(&source, None));

        fs::write(&header, "int get_val() { return 2; }").expect("modify helper.h");
        assert!(manager.is_changed(&source, None));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_cache.py::test_cache_commented_header_dependency`.
    #[test]
    fn test_cache_commented_header_dependency() {
        let tmp = make_temp_dir("commented_hdr");
        let manager = make_manager(&tmp);
        let source = tmp.join("main.c");
        fs::write(
            &source,
            "\n    // #include \"commented_one.h\"\n    /* #include \"commented_two.h\" */\n    int main() { return 0; }\n    ",
        )
        .expect("write main.c");

        let includes = manager.get_c_includes(&source);
        assert!(includes.is_empty());

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_cache.py::test_cache_concurrent_updates`.
    #[test]
    fn test_cache_concurrent_updates() {
        let tmp = make_temp_dir("concurrent");
        let manager = Arc::new(make_manager(&tmp));

        let mut files = Vec::new();
        for i in 0..10 {
            let f = tmp.join(format!("file_{i}.c"));
            fs::write(&f, format!("int func_{i}() {{ return {i}; }}")).expect("write file");
            files.push(f);
        }

        let mut handles = Vec::new();
        for f in &files {
            let mgr = Arc::clone(&manager);
            let path = f.clone();
            handles.push(thread::spawn(move || mgr.update_cache(&path, None)));
        }
        for h in handles {
            h.join().expect("thread join");
        }

        for f in &files {
            assert!(!manager.is_changed(f, None));
        }

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_cache.py::test_cache_binary_verification_stem_collision`.
    #[test]
    fn test_cache_binary_verification_stem_collision() {
        let tmp = make_temp_dir("stem_collision");
        let manager = make_manager(&tmp);

        let c_source = tmp.join("hello.c");
        fs::write(&c_source, "int main() { return 0; }").expect("write hello.c");
        let cpp_source = tmp.join("hello.cpp");
        fs::write(&cpp_source, "int main() { return 1; }").expect("write hello.cpp");
        let out_bin = tmp.join("hello.out");

        fs::write(&out_bin, b"binary_compiled_from_cpp").expect("write bin");
        manager.update_cache(&cpp_source, Some(&out_bin));
        assert!(!manager.is_changed(&cpp_source, Some(&out_bin)));

        fs::write(&out_bin, b"binary_compiled_from_c").expect("overwrite bin");
        manager.update_cache(&c_source, Some(&out_bin));
        assert!(!manager.is_changed(&c_source, Some(&out_bin)));

        assert!(manager.is_changed(&cpp_source, Some(&out_bin)));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_cache.py::test_cache_clear_cleans_objs_dir`.
    #[test]
    fn test_cache_clear_cleans_objs_dir() {
        let tmp = make_temp_dir("clear_objs");
        let manager = make_manager(&tmp);
        let src = tmp.join("main.c");
        fs::write(&src, "int main() {}").expect("write main.c");

        let obj_path = manager.get_object_path(&src);
        fs::write(&obj_path, b"compiled_object_binary").expect("write obj");
        manager.update_cache(&src, Some(&obj_path));

        assert!(obj_path.exists());
        assert!(manager.objs_dir.exists());

        manager.clear();
        assert!(!obj_path.exists());
        assert!(!manager.objs_dir.exists());

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Port of `dev:tests/test_cache.py::test_cache_commented_header_at_eof_without_newline`.
    #[test]
    fn test_cache_commented_header_at_eof_without_newline() {
        let tmp = make_temp_dir("eof_comment");
        let manager = make_manager(&tmp);
        let source = tmp.join("main.c");
        fs::write(&source, b"int main() { return 0; }\n// #include \"never_exist.h\"")
            .expect("write main.c");

        let includes = manager.get_c_includes(&source);
        assert!(includes.is_empty());

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Verify R3 modernized cache path resolution (`$XDG_CACHE_HOME/run` or `~/.cache/run` / `%LOCALAPPDATA%\run`).
    #[test]
    fn test_cache_base_dir_r3_modern_paths() {
        let custom = cache_base_dir_from(Some(OsStr::new("/tmp/xdg_cache")), Some(OsStr::new("/home/u")));
        assert_eq!(custom, Some(PathBuf::from("/tmp/xdg_cache/run")));

        let fallback = cache_base_dir_from(None, Some(OsStr::new("/home/u")));
        #[cfg(windows)]
        assert_eq!(fallback, Some(PathBuf::from("/home/u/AppData/Local/run")));
        #[cfg(not(windows))]
        assert_eq!(fallback, Some(PathBuf::from("/home/u/.cache/run")));

        assert_eq!(cache_base_dir_from(None, None), None);
    }

    /// Verify recursive `#include "..."` cycle protection and legacy `cache.json` entry support.
    #[test]
    fn test_include_cycle_and_legacy_entry() {
        let tmp = make_temp_dir("cycle_legacy");
        let manager = make_manager(&tmp);

        let a = tmp.join("a.h");
        let b = tmp.join("b.h");
        let main_c = tmp.join("main.c");
        fs::write(&a, "#include \"b.h\"\nint fa();\n").expect("write a.h");
        fs::write(&b, "#include \"a.h\"\nint fb();\n").expect("write b.h");
        fs::write(&main_c, "#include \"a.h\"\nint main() { return 0; }\n").expect("write main.c");

        let includes = manager.get_c_includes(&main_c);
        assert_eq!(includes.len(), 2);

        // Legacy string format in cache.json should deserialize cleanly and detect cache hits.
        let key = std::path::absolute(&main_c)
            .expect("abs path")
            .to_string_lossy()
            .into_owned();
        let hash = manager.get_composite_hash(&main_c);
        fs::create_dir_all(&manager.cache_dir).expect("create cache dir");
        let legacy_json = serde_json::json!({ key: hash }).to_string();
        fs::write(&manager.cache_file, legacy_json).expect("write legacy cache.json");

        let reloaded = make_manager(&tmp);
        assert!(!reloaded.is_changed(&main_c, None));

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Regression test: trailing slashes on `project_root` hash to the exact same cache bucket.
    #[test]
    fn test_trailing_slash_cache_bucket_equivalence() {
        let tmp = make_temp_dir("trailing_slash");
        let proj = tmp.join("proj");
        fs::create_dir_all(&proj).expect("mkdir proj");
        let cache_base = tmp.join("cache_home").join("run");

        let with_no_slash = CacheManager::with_cache_base(&proj, &cache_base).expect("no slash");
        let trailing_str = format!("{}/", proj.display());
        let with_slash =
            CacheManager::with_cache_base(Path::new(&trailing_str), &cache_base).expect("slash");
        assert_eq!(with_no_slash.cache_dir, with_slash.cache_dir);

        let _ = fs::remove_dir_all(&tmp);
    }

    /// Regression test: `clear()` unlinks dangling symlinks in `objs/`/`bin/`/`src/` and removes empty directories.
    #[cfg(unix)]
    #[test]
    fn test_clear_removes_dangling_symlinks() {
        let tmp = make_temp_dir("clear_dangling");
        let manager = make_manager(&tmp);
        fs::create_dir_all(&manager.objs_dir).expect("mkdir objs");
        fs::create_dir_all(&manager.bin_dir).expect("mkdir bin");
        fs::create_dir_all(&manager.src_dir).expect("mkdir src");

        let obj_link = manager.objs_dir.join("dangling.o");
        let bin_link = manager.bin_dir.join("dangling.out");
        let src_link = manager.src_dir.join("dangling.c");
        std::os::unix::fs::symlink(tmp.join("missing_obj"), &obj_link).expect("symlink obj");
        std::os::unix::fs::symlink(tmp.join("missing_bin"), &bin_link).expect("symlink bin");
        std::os::unix::fs::symlink(tmp.join("missing_src"), &src_link).expect("symlink src");

        manager.clear();
        assert!(fs::symlink_metadata(&obj_link).is_err());
        assert!(fs::symlink_metadata(&bin_link).is_err());
        assert!(fs::symlink_metadata(&src_link).is_err());
        assert!(!manager.objs_dir.exists());
        assert!(!manager.bin_dir.exists());
        assert!(!manager.src_dir.exists());
        assert!(!manager.cache_dir.exists());

        let _ = fs::remove_dir_all(&tmp);
    }

    /// White-box test: corrupted `cache.json` recovers gracefully without panicking and
    /// rewrites valid metadata on the next `update_cache`.
    #[test]
    fn test_corrupted_cache_json_recovery_and_missing_binary_invalidation() {
        let tmp = make_temp_dir("corrupt_json");
        let manager = make_manager(&tmp);
        let src = tmp.join("app.c");
        fs::write(&src, "int main(void) { return 0; }\n").unwrap();

        fs::create_dir_all(&manager.cache_dir).unwrap();
        fs::write(&manager.cache_file, "{not valid json!!!").unwrap();

        // Corrupted JSON must be treated as changed rather than panicking.
        assert!(manager.is_changed(&src, None));

        let bin = manager.get_cached_binary_path(&src);
        fs::create_dir_all(bin.parent().unwrap()).unwrap();
        fs::write(&bin, b"BIN").unwrap();
        manager.update_cache(&src, Some(&bin));
        assert!(!manager.is_changed(&src, Some(&bin)));

        // Deleting the cached binary invalidates the hit even though source hash matches.
        fs::remove_file(&bin).unwrap();
        assert!(manager.is_changed(&src, Some(&bin)));

        // Calling clear() twice (second time on missing dir) is idempotent.
        manager.clear();
        manager.clear();

        let _ = fs::remove_dir_all(&tmp);
    }
}
