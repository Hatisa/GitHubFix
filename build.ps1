$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path "assets\githubfix.ico")) {
    python tools\make_icon.py
}

$common = @(
    "--noconfirm", "--clean", "--onefile",
    "--icon", "assets\githubfix.ico",
    "--version-file", "version_info.txt",
    "--paths", "src",
    "--add-data", "assets;assets",
    "--hidden-import", "github_fix"
)
python -m PyInstaller @common --windowed --uac-admin --name GitHubFix "run_app.py"
if ($LASTEXITCODE -ne 0) { throw "GUI build failed" }

python -m PyInstaller @common --console --name GitHubFix-CLI "run_app.py"
if ($LASTEXITCODE -ne 0) { throw "CLI build failed" }

Write-Host ""
Write-Host "构建完成:"
Get-ChildItem "dist" | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
