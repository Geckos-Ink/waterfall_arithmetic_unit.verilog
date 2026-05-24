# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Start the vJTAG MMIO TCL server (quartus_stp). Leave running in its own
# console; the python benchmark connects to it over TCP.
[CmdletBinding()]
param(
    [int]    $Port = 2540,
    [int]    $Instance = 0,
    [string] $QuartusRoot = "C:\altera_standard\25.1std"
)

$ErrorActionPreference = "Stop"

$DemoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$Tcl      = Join-Path $DemoRoot "host\tcl\wau_jtag_server.tcl"
if (-not (Test-Path $Tcl)) { throw "$Tcl not found" }

$stp = Join-Path $QuartusRoot "quartus\bin64\quartus_stp.exe"
if (-not (Test-Path $stp)) { throw "quartus_stp.exe not found at $stp" }

Write-Host "Starting vJTAG MMIO server on TCP $Port (instance=$Instance) ..." -ForegroundColor Cyan
& $stp -t $Tcl --port $Port --instance $Instance
