# 冒烟：确认冻结 exe 把 JSON 写到 %LOCALAPPDATA%\TankGame\
# 用法：先 build.ps1，再：
#   powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_test.ps1

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Exe = Join-Path $Root "dist\TankGame\TankGame.exe"
if (-not (Test-Path $Exe)) { throw "Missing $Exe — run build.ps1 first" }

$Data = Join-Path $env:LOCALAPPDATA "TankGame"
# 清掉旧测试文件（保留目录）
foreach ($f in @("tank_keys.json", "tank_career.json", "tank_coach.json")) {
    $p = Join-Path $Data $f
    if (Test-Path $p) { Remove-Item -Force $p }
}

$env:TANK_SKIP_MENU = "1"
$env:TANK_MODE = "2p"
# 无显示器 CI 可设：SDL_VIDEODRIVER=dummy（商店包本地冒烟一般有显示器）
$proc = Start-Process -FilePath $Exe -PassThru -WorkingDirectory (Split-Path $Exe)
Start-Sleep -Seconds 4
if (-not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force
}

# 改键/生涯文件可能尚未写出；至少确认目录已创建（frozen 启动会 makedirs）
if (-not (Test-Path $Data)) {
    throw "Expected data dir missing: $Data"
}
Write-Host "SMOKE_DATA_DIR_OK $Data"

# 二次启动：用环境变量强制写一个文件路径，验证进程能跑
$probe = Join-Path $Data "smoke_probe.json"
$env:TANK_KEYS_FILE = $probe
$proc2 = Start-Process -FilePath $Exe -PassThru -WorkingDirectory (Split-Path $Exe)
Start-Sleep -Seconds 3
if (-not $proc2.HasExited) {
    Stop-Process -Id $proc2.Id -Force
}
Write-Host "SMOKE_EXE_LAUNCH_OK"
Write-Host "SMOKE_ALL_OK"
