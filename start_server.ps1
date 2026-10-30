$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $scriptDir

$pythonExe = Join-Path $scriptDir 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-Error "Python virtual environment was not found at $pythonExe"
    exit 1
}

& $pythonExe .\server.py
