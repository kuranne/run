# Auto Compiler & Runner Windows Setup Script (setup.ps1)

$ErrorActionPreference = "Stop"

# --- Python Check --- #
Write-Host "-- Checking for python..."
$PythonCmd = if (Get-Command python -ErrorAction SilentlyContinue) {
    "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    "py"
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    "python3"
} else {
    Write-Error "[ ERROR ] Can't execute python"
    exit 1
}

# Check Python Version (>= 3.11)
& $PythonCmd -c "import sys; exit(0) if sys.version_info >= (3, 11) else exit(1)"
if ($LASTEXITCODE -ne 0) {
    Write-Error "[ ERROR ] Python 3.11+ required"
    exit 1
}

# --- Virtual Environment --- #
Write-Host "-- Setting up virtual environment..."
$CurrentDir = $PSScriptRoot
if (-not $CurrentDir) {
    $CurrentDir = (Get-Location).Path
}

$VenvDir = Join-Path $CurrentDir ".venv"
if (Test-Path $VenvDir) {
    Remove-Item -Recurse -Force $VenvDir
}
& $PythonCmd -m venv $VenvDir

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"

# --- Install Dependencies --- #
Write-Host "-- Installing dependencies..."
& $VenvPip install .

# --- Create Wrapper Script --- #
Write-Host "-- Creating runner script..."
$RunCmd = Join-Path $CurrentDir "run.cmd"
$CmdContent = "@`"$VenvPython`" `"$CurrentDir\src\main.py`" %*"
Set-Content -Path $RunCmd -Value $CmdContent -Encoding ASCII

$RunPs1 = Join-Path $CurrentDir "run.ps1"
$PsContent = "& `"$VenvPython`" `"$CurrentDir\src\main.py`" @args"
Set-Content -Path $RunPs1 -Value $PsContent -Encoding UTF8

# --- Add to PATH --- #
Write-Host "-- Configuring User PATH..."
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$PathEntries = if ($UserPath) { $UserPath -split ';' } else { @() }

if ($PathEntries -notcontains $CurrentDir) {
    $NewUserPath = if ($UserPath) { "$UserPath;$CurrentDir" } else { $CurrentDir }
    [Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
    $env:Path = "$env:Path;$CurrentDir"
    Write-Host "[ INFO ] Added $CurrentDir to User PATH"
}

# --- Complete --- #
Write-Host "[ SUCCESS ] Setup complete!"
Write-Host "You can now use 'run' command (restart terminal if needed)."
