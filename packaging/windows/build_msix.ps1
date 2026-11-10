# 把 dist\TankGame onedir 打成 MSIX（需 Windows SDK：makeappx / signtool）
# 用法（先 build.ps1）：
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build_msix.ps1
# 可选：-Publisher "CN=YourName"；-Version 不填则读取仓库根 tank_version.py
# （三段式自动补成 MSIX 要求的四段式）

param(
    [string]$Publisher = "CN=TankGameDev",
    [string]$Version = "",
    [string]$IdentityName = "TankGameDev.TankGame"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")

# 版本号唯一来源：仓库根 tank_version.py（不要再手写；_selftest_version.py 会检查）
if (-not $Version) {
    $VersionPy = Join-Path $Root "tank_version.py"
    $versionSrc = [IO.File]::ReadAllText($VersionPy, [Text.Encoding]::UTF8)
    $match = [regex]::Match($versionSrc, '__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"')
    if (-not $match.Success) { throw "Cannot read __version__ from $VersionPy" }
    $Version = $match.Groups[1].Value
}
# MSIX / AppxManifest 要求四段数字版本
if (($Version -split '\.').Count -lt 4) { $Version = "$Version.0" }
if ($Version -notmatch '^\d+\.\d+\.\d+\.\d+$') {
    throw "Invalid MSIX version: $Version (expected Major.Minor.Build.Revision)"
}
$Stage = Join-Path $Root "dist\msix_payload"
$OutMsix = Join-Path $Root "dist\TankGame.msix"
$ManifestSrc = Join-Path $PSScriptRoot "msix\AppxManifest.xml"
$AssetsSrc = Join-Path $PSScriptRoot "msix\Assets"
$ExeDir = Join-Path $Root "dist\TankGame"

if (-not (Test-Path (Join-Path $ExeDir "TankGame.exe"))) {
    throw "Missing dist\TankGame\TankGame.exe — run build.ps1 first"
}

$MakeAppx = Get-Command makeappx.exe -ErrorAction SilentlyContinue
if (-not $MakeAppx) {
    $sdk = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin" -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName "x64\makeappx.exe" } |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1
    if ($sdk) { $MakeAppx = $sdk } else { throw "makeappx.exe not found. Install Windows SDK." }
}

if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Path $Stage | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Stage "TankGame") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Stage "Assets") | Out-Null

Copy-Item -Recurse -Force (Join-Path $ExeDir "*") (Join-Path $Stage "TankGame")
Copy-Item -Force $ManifestSrc (Join-Path $Stage "AppxManifest.xml")

# 替换 Identity（简易字符串替换）
$manifestPath = Join-Path $Stage "AppxManifest.xml"
# 必须按 UTF-8 读：Windows PowerShell 5.1 的 Get-Content -Raw 默认按 ANSI 解码，
# 会把中文注释/描述读成乱码，甚至吞掉标签，导致 makeappx 清单校验失败。
$xml = [IO.File]::ReadAllText($manifestPath, [Text.Encoding]::UTF8)
$xml = $xml -replace 'Name="YourPublisher\.TankGame"', "Name=`"$IdentityName`""
$xml = $xml -replace 'Publisher="CN=YourPublisher"', "Publisher=`"$Publisher`""
# 只替换 <Identity> 里的 Version，别误伤其它标签
$xml = [regex]::Replace(
    $xml,
    '(<Identity\b[^>]*\bVersion=")[^"]+(")',
    '${1}' + $Version + '${2}'
)
# 无 BOM，避免 makeappx 解析清单失败
$utf8 = New-Object System.Text.UTF8Encoding $false
[IO.File]::WriteAllText($manifestPath, $xml, $utf8)

# 资源：若无正式 PNG，用占位图（Partner Center 上架前需换成合规尺寸素材）
if (Test-Path $AssetsSrc) {
    Copy-Item -Force (Join-Path $AssetsSrc "*") (Join-Path $Stage "Assets") -ErrorAction SilentlyContinue
}
$required = @(
    "StoreLogo.png",
    "Square150x150Logo.png",
    "Square44x44Logo.png",
    "Wide310x150Logo.png",
    "SplashScreen.png"
)
foreach ($name in $required) {
    $dest = Join-Path $Stage "Assets\$name"
    if (-not (Test-Path $dest)) {
        # 最小合法 PNG 占位（1x1）；商店审核前必须替换为正确尺寸
        $bytes = [Convert]::FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        [IO.File]::WriteAllBytes($dest, $bytes)
        Write-Warning "Placeholder asset written: $dest — replace before Store submission"
    }
}

if (Test-Path $OutMsix) { Remove-Item -Force $OutMsix }
& $MakeAppx pack /d $Stage /p $OutMsix /o
if ($LASTEXITCODE -ne 0) {
    throw "makeappx failed (exit $LASTEXITCODE) — see output above"
}
Write-Host "==> MSIX: $OutMsix"
Write-Host "Sign with signtool (Store upload often uses Partner Center signing) before sideload."
Write-Host "See packaging/windows/STORE.md for Partner Center steps."
