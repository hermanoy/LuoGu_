param([switch]$WithUpload)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
if ($LASTEXITCODE -ne 0) { throw "Install Python 3.11 or newer." }
if (-not (Test-Path '.venv\Scripts\python.exe')) { python -m venv .venv }
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Get-Command g++ -ErrorAction SilentlyContinue)) {
    if (Test-Path 'C:\msys64\ucrt64\bin\g++.exe') { $env:PATH = "C:\msys64\ucrt64\bin;$env:PATH" }
}
if ($WithUpload) {
    & $python -m pip install -e '.[upload]'
    if ($LASTEXITCODE -ne 0) { throw "Dependency install failed." }
    & $python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "Browser install failed." }
}
if (-not (Test-Path 'settings.toml')) { Copy-Item 'settings.example.toml' 'settings.toml' }
New-Item -ItemType Directory -Force 'inbox' | Out-Null
& $python main.py --doctor
if ($LASTEXITCODE -ne 0) { throw "Read the diagnostic above and fix the missing prerequisites." }
Write-Host 'Ready. Run run.cmd; it will ask for an API key if no environment key is configured.'
