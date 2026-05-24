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
$Sof = Join-Path $DemoRoot "quartus\output_files\wau_de0_nano_basic.sof"
if (-not (Test-Path $Sof)) {
    throw "$Sof not found. Run scripts\build.ps1 first."
}

$pgm = Join-Path $QuartusRoot "quartus\bin64\quartus_pgm.exe"
if (-not (Test-Path $pgm)) {
    throw "quartus_pgm.exe not found at $pgm"
}

Write-Host "Programming $Sof onto cable #$Cable via USB-Blaster ..." -ForegroundColor Cyan
& $pgm -c $Cable -m jtag -o "p;$Sof"
if ($LASTEXITCODE -ne 0) { throw "quartus_pgm failed (exit $LASTEXITCODE)" }

Write-Host "Programming OK." -ForegroundColor Green
