# 在 Windows 上构建 TankGame onedir 包（需已安装 Python 3.11+）
# 用法（仓库 tank/ 根目录）：
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
# 产物：dist\TankGame\TankGame.exe

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root

Write-Host "==> Root: $Root"
python --version
python -m pip install -r packaging\windows\requirements-build.txt

if (Test-Path dist\TankGame) { Remove-Item -Recurse -Force dist\TankGame }
if (Test-Path build\tank_game) { Remove-Item -Recurse -Force build\tank_game }

python -m PyInstaller packaging\windows\tank_game.spec --noconfirm
if (-not (Test-Path dist\TankGame\TankGame.exe)) {
    throw "Build failed: dist\TankGame\TankGame.exe missing"
}

Write-Host "==> Built: dist\TankGame\TankGame.exe"
Write-Host "==> Smoke: set TANK_SKIP_MENU=1 and run briefly, or use packaging\windows\smoke_test.ps1"
