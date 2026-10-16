param([string]$InnoCompiler = '')
# Run only after an explicit user request to package the campus edition.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $InnoCompiler) {
    $localCompiler = Join-Path $projectRoot '.runtime\inno\ISCC.exe'
    if (Test-Path -LiteralPath $localCompiler) { $InnoCompiler = $localCompiler }
    else {
        $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($command) { $InnoCompiler = $command.Source }
    }
}
if (-not $InnoCompiler -or -not (Test-Path -LiteralPath $InnoCompiler)) {
    throw 'Install Inno Setup 6.3+ or pass -InnoCompiler with the ISCC.exe path.'
}
Push-Location -LiteralPath $projectRoot
try {
    & python -m PyInstaller --noconfirm --distpath dist --workpath build/campus packaging/CampusDutyManager.spec
    if ($LASTEXITCODE -ne 0) { throw 'Campus PyInstaller build failed.' }
    & python tools/package_campus_assets.py
    if ($LASTEXITCODE -ne 0) { throw 'Campus release asset copy failed.' }
    & $InnoCompiler /Qp (Join-Path $projectRoot 'packaging\campus-installer.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Campus installer build failed.' }
    $outputRoot = Join-Path $projectRoot 'output\campus'
    $archive = Join-Path $outputRoot 'CampusDutyManager-Portable-0.1.0.zip'
    Compress-Archive -LiteralPath (Join-Path $projectRoot 'dist\CampusDutyManager') -DestinationPath $archive -Force
    $installer = Join-Path $outputRoot 'CampusDutyManager-Setup-0.1.0.exe'
    Copy-Item -LiteralPath (Join-Path $projectRoot 'packaging\campus-readme.txt') -Destination (Join-Path $outputRoot 'CampusDuty-Readme.txt') -Force
    $checksums = foreach ($file in @($installer, $archive)) {
        $hash = Get-FileHash -LiteralPath $file -Algorithm SHA256
        '{0}  {1}' -f $hash.Hash.ToLowerInvariant(), (Split-Path -Leaf $file)
    }
    $checksums | Set-Content -LiteralPath (Join-Path $outputRoot 'SHA256SUMS.txt') -Encoding ascii
    Get-Item -LiteralPath $installer, $archive | Select-Object FullName, Length
} finally { Pop-Location }
