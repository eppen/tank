# 把 dist\TankGame onedir 打成 MSIX（需 Windows SDK：makeappx / signtool）
# 用法（先 build.ps1）：
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build_msix.ps1
# 可选：-Publisher "CN=YourName" -Version "1.0.0.0"

param(
    [string]$Publisher = "CN=TankGameDev",
    [string]$Version = "1.0.0.0",
    [string]$IdentityName = "TankGameDev.TankGame"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
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
$xml = Get-Content $manifestPath -Raw
$xml = $xml -replace 'Name="YourPublisher\.TankGame"', "Name=`"$IdentityName`""
$xml = $xml -replace 'Publisher="CN=YourPublisher"', "Publisher=`"$Publisher`""
$xml = $xml -replace 'Version="1\.0\.0\.0"', "Version=`"$Version`""
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
Write-Host "==> MSIX: $OutMsix"
Write-Host "Sign with signtool (Store upload often uses Partner Center signing) before sideload."
Write-Host "See packaging/windows/STORE.md for Partner Center steps."
