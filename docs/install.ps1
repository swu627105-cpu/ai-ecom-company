# AI 电商公司平台 · 一键安装器
# 作用：1) 找到或下载应用  2) 注册 aiecom:// 启动协议  3) 启动应用
# 只写当前用户注册表（HKCU），不需要管理员权限。

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Version   = 'v0.1.0'
$ZipName   = 'AI-Ecom-Company-0.1.0-Windows-x64.zip'
$ReleaseBase = "https://github.com/swu627105-cpu/ai-ecom-company/releases/download/$Version/"
$SiteUrl   = 'https://swu627105-cpu.github.io/ai-ecom-company/'
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\AI-Ecom-Company'

function Say($msg, $color = 'Gray') { Write-Host "  $msg" -ForegroundColor $color }

Write-Host ''
Write-Host '  ============================================' -ForegroundColor DarkCyan
Write-Host '   AI 电商公司平台 · 一键安装' -ForegroundColor Green
Write-Host '  ============================================' -ForegroundColor DarkCyan
Write-Host ''

# ---------- 1. 找应用 ----------
Say '第 1 步 / 3 · 查找本机是否已有应用……' 'Cyan'
$candidates = New-Object System.Collections.ArrayList

try {
    $prev = (Get-ItemProperty -Path 'HKCU:\Software\AIEcom' -Name 'ExePath' -ErrorAction Stop).ExePath
    if ($prev) { [void]$candidates.Add($prev) }
} catch { }

[void]$candidates.Add((Join-Path $InstallDir 'AI-Ecom-Company.exe'))
[void]$candidates.Add((Join-Path $InstallDir 'AI-Ecom-Company-Windows-x64\AI-Ecom-Company.exe'))

foreach ($root in @($env:USERPROFILE + '\Desktop', $env:USERPROFILE + '\Downloads')) {
    if (Test-Path -LiteralPath $root) {
        try {
            Get-ChildItem -LiteralPath $root -Filter 'AI-Ecom-Company.exe' -Recurse -Depth 4 -File -ErrorAction SilentlyContinue |
                ForEach-Object { [void]$candidates.Add($_.FullName) }
        } catch { }
    }
}

$exe = $null
foreach ($c in $candidates) {
    if ($c -and (Test-Path -LiteralPath $c)) { $exe = $c; break }
}

if ($exe) {
    Say ("已找到：" + $exe) 'Green'
} else {
    Say '本机没有找到应用，开始下载完整离线包（约 178 MB）……' 'Yellow'
    $zipPath = Join-Path $env:TEMP $ZipName
    $url = $ReleaseBase + $ZipName

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & curl.exe -L --fail --retry 2 -o "$zipPath" "$url"
        if ($LASTEXITCODE -ne 0) { throw "下载失败（curl 退出码 $LASTEXITCODE）" }
    } else {
        Say '正在用备用方式下载，请耐心等待……' 'Gray'
        (New-Object System.Net.WebClient).DownloadFile($url, $zipPath)
    }

    $sizeMb = [math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 1)
    Say "下载完成：$sizeMb MB" 'Green'

    Say '正在解压……' 'Cyan'
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Expand-Archive -LiteralPath $zipPath -DestinationPath $InstallDir -Force
    Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue

    $exe = Get-ChildItem -LiteralPath $InstallDir -Filter 'AI-Ecom-Company.exe' -Recurse -File |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $exe) { throw '解压完成但没找到 AI-Ecom-Company.exe，请改用手动下载。' }
    Say ("已安装到：" + $exe) 'Green'
}

# ---------- 2. 注册启动协议 ----------
Say '第 2 步 / 3 · 注册 aiecom:// 启动协议……' 'Cyan'
New-Item -Path 'HKCU:\Software\AIEcom' -Force | Out-Null
Set-ItemProperty -Path 'HKCU:\Software\AIEcom' -Name 'ExePath' -Value $exe

New-Item -Path 'HKCU:\Software\Classes\aiecom\shell\open\command' -Force | Out-Null
Set-ItemProperty -Path 'HKCU:\Software\Classes\aiecom' -Name '(default)' -Value 'URL:AI-Ecom-Company Launcher'
New-ItemProperty -Path 'HKCU:\Software\Classes\aiecom' -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
Set-ItemProperty -Path 'HKCU:\Software\Classes\aiecom\shell\open\command' -Name '(default)' -Value ('"' + $exe + '"')
Say '注册完成。' 'Green'

# ---------- 3. 启动 ----------
Say '第 3 步 / 3 · 启动应用……' 'Cyan'
$workDir = Split-Path -Parent $exe
try {
    Start-Process -FilePath $exe -WorkingDirectory $workDir
    Say '已启动，首次进入约需 10-20 秒。' 'Green'
} catch {
    Say ('启动失败：' + $_.Exception.Message) 'Red'
}

Write-Host ''
Write-Host '  以后直接打开这个网址点按钮即可启动：' -ForegroundColor White
Write-Host "  $SiteUrl" -ForegroundColor Cyan
Write-Host ''
