#Requires -Version 7.0
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$iconPath = Join-Path $projectRoot "assets/SaveLC.ico"
$previousBytecodePreference = $env:PYTHONDONTWRITEBYTECODE

Push-Location -LiteralPath $projectRoot
try {
    $env:PYTHONDONTWRITEBYTECODE = "1"
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --icon $iconPath --add-data "$iconPath;assets" --specpath build --name SaveLiveCaptions src/main.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 构建失败，退出码：$LASTEXITCODE"
    }
    Write-Output (Join-Path $projectRoot "dist/SaveLiveCaptions.exe")
}
finally {
    $env:PYTHONDONTWRITEBYTECODE = $previousBytecodePreference
    Pop-Location
}
