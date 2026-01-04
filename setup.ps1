#--- Python Setup ---#
Write-Host "Checking for python..." -ForegroundColor Cyan
if (!(Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Can't execute python"; exit 1
}

Write-Host "Setting up virtual environment..." -ForegroundColor Cyan
python -m venv .venv 
& .\.venv\Scripts\Activate.ps1

Write-Host "Installing requirements..." -ForegroundColor Cyan
pip install -r requirements.txt

#--- Compile?? ---#
Write-Host "Compiling to Executable..." -ForegroundColor Cyan
pyinstaller --onefile run.py

#--- Clear & Clean ---#
if (Test-Path "./dist/run.exe") {
    Move-Item -Path "./dist/run.exe" -Destination "./run.exe" -Force
}

deactivate

Remove-Item -Path "./build", "./dist", "./.venv" -Recurse -ErrorAction SilentlyContinue
Remove-Item -Path "./run.spec" -ErrorAction SilentlyContinue

#--- Add to PATH ---#
$currentDir = Get-Location | Select-Object -ExpandProperty Path
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")

if ($userPath -split ';' -notcontains $currentDir) {
    $newPath = "$userPath;$currentDir".TrimStart(';')
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
}

Write-Host "Setup complete!" -ForegroundColor Green