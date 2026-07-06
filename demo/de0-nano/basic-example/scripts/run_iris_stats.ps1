# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Run the real-dataset iris morphology benchmark against an already-running
# quartus_stp TCL server.
[CmdletBinding()]
param(
    [int]    $Port = 2540,
    [string] $Server = "localhost",
    [string] $Report = ""
)

$ErrorActionPreference = "Stop"

$DemoRoot = (Resolve-Path "$PSScriptRoot\..").Path

if ([string]::IsNullOrEmpty($Report)) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $Report = Join-Path $DemoRoot "build\iris_benchmark_$stamp.json"
}

$env:PYTHONPATH = Join-Path $DemoRoot "host"
$pyArgs = @(
    (Join-Path $DemoRoot "host\programs\run_iris_stats_benchmark.py"),
    "--host", $Server,
    "--port", $Port,
    "--report", $Report
)

Write-Host "python $($pyArgs -join ' ')" -ForegroundColor Cyan
& python @pyArgs
exit $LASTEXITCODE
