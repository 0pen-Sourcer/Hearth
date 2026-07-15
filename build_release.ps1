# Build BOTH editions (Full + Lite) and BOTH installers in one shot.
#
# Fixes the env-bleed that once made both editions come out as Lite: HEARTH_LITE
# is explicitly CLEARED before the Full pass and SET before the Lite pass, in a
# single session. Each edition is moved to its own dist_<edition>\Hearth so the
# second build can't clobber the first, then Inno Setup packages each.
#
#   Full  -> dist_full\Hearth  -> Output\Hearth-Setup-v0.7.0-preview.exe
#   Lite  -> dist_lite\Hearth  -> Output\Hearth-Setup-Lite-v0.7.0-preview.exe
#
# Run with Hearth fully closed (tray Quit) so no bundled file is locked.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
$iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { throw "Inno Setup not found at $iscc - install it or fix the path." }

# Antivirus (AVG/Defender) scans freshly-built .exe files and briefly LOCKS them,
# which made a plain Remove-Item/Move-Item fail with "Access denied" right after a
# successful build. Retry through the transient lock instead of aborting the whole
# run. (Best long-term fix: exclude this folder in your AV, but this is resilient.)
function Retry-IO([scriptblock]$act, [string]$what) {
    for ($i = 0; $i -lt 12; $i++) {
        try { & $act; return } catch { Start-Sleep -Seconds 3 }
    }
    throw "$what still failed after retries - a file is locked (AV scan / Explorer open on the folder). Close them and re-run."
}

function Build-Edition([bool]$lite, [string]$destTop) {
    $tag = if ($lite) { 'LITE' } else { 'FULL' }
    Write-Host "=== Building $tag ===" -ForegroundColor Cyan
    foreach ($d in @('build', 'dist', $destTop)) {
        if (Test-Path $d) { Retry-IO { Remove-Item $d -Recurse -Force -ErrorAction Stop } "remove $d" }
    }
    if ($lite) { $env:HEARTH_LITE = '1' } else { Remove-Item Env:HEARTH_LITE -ErrorAction SilentlyContinue }
    & $py -m PyInstaller Hearth.spec --clean --noconfirm
    $code = $LASTEXITCODE
    Remove-Item Env:HEARTH_LITE -ErrorAction SilentlyContinue   # never let it leak to the next pass
    if ($code -ne 0) { throw "PyInstaller failed for $tag (exit $code)" }
    $cli = "dist\Hearth\_internal\dist_cli_launcher.bat"
    if (Test-Path $cli) { Retry-IO { Move-Item -Force $cli "dist\Hearth\Hearth-cli.bat" -ErrorAction Stop } "rename cli launcher" }
    Retry-IO { Move-Item dist $destTop -ErrorAction Stop } "move dist -> $destTop"   # dist\Hearth -> dist_<edition>\Hearth
    Write-Host "  $tag bundle -> $destTop\Hearth" -ForegroundColor DarkGray
}

Build-Edition $false "dist_full"
Build-Edition $true  "dist_lite"

Write-Host "=== Packaging installers ===" -ForegroundColor Cyan
& $iscc /DSrcDir=dist_full\Hearth Hearth.iss
if ($LASTEXITCODE -ne 0) { throw "Full installer failed" }
& $iscc /DSrcDir=dist_lite\Hearth /DEditionSuffix=-Lite "/DEditionLabel= Lite" Hearth.iss
if ($LASTEXITCODE -ne 0) { throw "Lite installer failed" }

Write-Host "=== Done ===" -ForegroundColor Green
Get-ChildItem Output\*.exe | Select-Object Name, @{ n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } } | Format-Table -AutoSize
