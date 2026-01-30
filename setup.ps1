#--- Python Setup ---#
Write-Host "Checking for python..." -ForegroundColor Cyan
if (!(Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Can't execute python"; exit 1
}

# Check Python Version (>= 3.11)
$pyVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$maj, $min = $pyVer.Split('.')
if ([int]$maj -lt 3 -or ([int]$maj -eq 3 -and [int]$min -lt 11)) {
    Write-Error "Python 3.11+ is required. Found $pyVer"
    exit 1
}

Write-Host "Setting up virtual environment..." -ForegroundColor Cyan
if (!(Test-Path ".venv")) {
    python -m venv .venv
}

#--- Install Dependencies ---#
Write-Host "Installing dependencies..." -ForegroundColor Cyan
.venv\Scripts\pip install .

#--- Create Wrapper ---#
Write-Host "Creating run.cmd wrapper..." -ForegroundColor Cyan
$batchContent = "@`"%~dp0.venv\Scripts\python.exe`" `"%~dp0src\main.py`" %*"
Set-Content -Path "run.cmd" -Value $batchContent

#--- Add to PATH ---#
$currentDir = Get-Location | Select-Object -ExpandProperty Path
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")

if ($userPath -split ';' -notcontains $currentDir) {
    Write-Host "Adding $currentDir to User Path..." -ForegroundColor Yellow
    $newPath = "$userPath;$currentDir".TrimStart(';')
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
}

Write-Host "Setup complete! You may need to restart your terminal." -ForegroundColor Green