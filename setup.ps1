# Build and install the Rust `run` CLI binary on Windows.
$ErrorActionPreference = "Stop"

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Write-Error "cargo (Rust toolchain) is required. Install from https://rustup.rs"
    exit 1
}

Write-Host "Building run (release)..."
cargo build --release

$BinDir = Join-Path $env:USERPROFILE ".local\bin"
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
Copy-Item -Force "target\release\run.exe" (Join-Path $BinDir "run.exe")

Write-Host "Installed run.exe to $BinDir"
if ($env:PATH -notlike "*$BinDir*") {
    Write-Host "Add '$BinDir' to your User PATH to invoke 'run' globally."
}
