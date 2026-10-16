param([string]$InnoCompiler = '')

# Run only after an explicit user request to package the application.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $InnoCompiler) {
    $localCompiler = Join-Path $projectRoot '.runtime\inno\ISCC.exe'
    if (Test-Path -LiteralPath $localCompiler) {
        $InnoCompiler = $localCompiler
    } else {
        $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($command) { $InnoCompiler = $command.Source }
    }
}
if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler)) {
    throw '请安装 Inno Setup 6.3+，或使用 -InnoCompiler 指定 ISCC.exe。'
}
Push-Location -LiteralPath $projectRoot
try {
    & python -m PyInstaller --noconfirm --distpath dist --workpath build packaging/DutyManager.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 构建未完成。' }
    & python tools/package_assets.py
    if ($LASTEXITCODE -ne 0) { throw '发布资源整理未完成。' }
    & $InnoCompiler /Qp (Join-Path $projectRoot 'packaging\installer.iss')
    if ($LASTEXITCODE -ne 0) { throw '安装程序构建未完成。' }
    $archive = Join-Path $projectRoot 'output\DutyManager-Portable-0.1.0.zip'
    Compress-Archive -LiteralPath (Join-Path $projectRoot 'dist\DutyManager') -DestinationPath $archive -Force
    $installer = Join-Path $projectRoot 'output\DutyManager-Setup-0.1.0.exe'
    Copy-Item -LiteralPath (Join-Path $projectRoot 'packaging\使用说明.txt') -Destination (Join-Path $projectRoot 'output\使用说明.txt') -Force
    $checksums = foreach ($file in @($installer, $archive)) {
        $hash = Get-FileHash -LiteralPath $file -Algorithm SHA256
        '{0}  {1}' -f $hash.Hash.ToLowerInvariant(), (Split-Path -Leaf $file)
    }
    $checksums | Set-Content -LiteralPath (Join-Path $projectRoot 'output\SHA256SUMS.txt') -Encoding ascii
    Get-Item -LiteralPath $installer, $archive | Select-Object FullName, Length
} finally {
    Pop-Location
}
