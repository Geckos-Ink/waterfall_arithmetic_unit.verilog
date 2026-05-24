# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Run the python benchmark against an already-running TCL server.
[CmdletBinding()]
param(
    [int]    $Iters = 256,
    [int]    $Port = 2540,
    [string] $Server = "localhost",
    [string] $Report = "",
    [switch] $IncludeCw
)

$ErrorActionPreference = "Stop"

$DemoRoot = (Resolve-Path "$PSScriptRoot\..").Path

if ([string]::IsNullOrEmpty($Report)) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $Report = Join-Path $DemoRoot "build\benchmark_$stamp.json"
}

$env:PYTHONPATH = Join-Path $DemoRoot "host"
$pyArgs = @(
    (Join-Path $DemoRoot "host\programs\run_benchmark.py"),
    "--host", $Server,
    "--port", $Port,
    "--iters", $Iters,
    "--report", $Report
)
if ($IncludeCw) { $pyArgs += "--include-cw-flow" }

Write-Host "python $($pyArgs -join ' ')" -ForegroundColor Cyan
& python @pyArgs
exit $LASTEXITCODE
