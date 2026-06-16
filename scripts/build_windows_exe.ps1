param(
    [string]$Name = "TelegramAdminGUI"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$PythonScript = Join-Path $RepoRoot "src\telegram_admin_gui_app.py"
$DistDir = Join-Path $RepoRoot "dist"
$BuildDir = Join-Path $RepoRoot "build"
$SpecDir = $RepoRoot
$Requirements = Join-Path $RepoRoot "requirements.txt"

if (-not (Test-Path $PythonScript)) {
    throw "Could not find $PythonScript"
}

if (-not (Test-Path $Requirements)) {
    "telethon>=1.36.0" | Set-Content -LiteralPath $Requirements -Encoding UTF8
}

python -m pip install -r $Requirements

python -m PyInstaller `
    --onefile `
    --windowed `
    --clean `
    --name $Name `
    --distpath $DistDir `
    --workpath $BuildDir `
    --specpath $SpecDir `
    $PythonScript

Write-Host ""
Write-Host "Built executable:"
Write-Host (Join-Path $DistDir "$Name.exe")
