# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Download wau_de0_nano_basic.sof onto the connected DE0-Nano via USB-Blaster.
#
# Usage:
#   .\scripts\program.ps1                 # JTAG download, RAM-only (volatile)
#   .\scripts\program.ps1 -Cable 2        # if multiple USB-Blasters are present
[CmdletBinding()]
param(
    [int]    $Cable = 1,
    [string] $QuartusRoot = "C:\altera_standard\25.1std"
)

$ErrorActionPreference = "Stop"

$DemoRoot = (Resolve-Path "$PSScriptRoot\..").Path
# Quartus 25.1std drops .sof directly into the project dir unless an
# output_files dir is explicitly configured.
$Sof = Join-Path $DemoRoot "quartus\wau_de0_nano_basic.sof"
if (-not (Test-Path $Sof)) {
    $alt = Join-Path $DemoRoot "quartus\output_files\wau_de0_nano_basic.sof"
    if (Test-Path $alt) { $Sof = $alt }
    else { throw "$Sof not found. Run scripts\build.ps1 first." }
}

$pgm = Join-Path $QuartusRoot "quartus\bin64\quartus_pgm.exe"
if (-not (Test-Path $pgm)) {
    throw "quartus_pgm.exe not found at $pgm"
}

Write-Host "Programming $Sof onto cable #$Cable via USB-Blaster ..." -ForegroundColor Cyan
# Quartus 25.1 tools emit a harmless "TBBmalloc" line to stderr at startup that
# ErrorActionPreference=Stop would turn into a terminating error; relax it here
# and rely on the real exit code.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $pgm -c $Cable -m jtag -o "p;$Sof"
$pgmExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($pgmExit -ne 0) { throw "quartus_pgm failed (exit $pgmExit)" }

Write-Host "Programming OK." -ForegroundColor Green
