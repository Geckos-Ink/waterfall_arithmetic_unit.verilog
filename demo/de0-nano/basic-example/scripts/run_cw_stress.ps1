# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Run the live CW stress benchmark against the currently programmed DE0-Nano.
[CmdletBinding()]
param(
    [string] $ServerHost = "localhost",
    [int] $Port = 2540,
    [int] $FlowId = 90,
    [int] $RandomIters = 128,
    [int] $RandomRange = 255,
    [int] $Seed = 0xC0FFEE,
    [string] $Config = "",
    [string] $Report = ""
)

$ErrorActionPreference = "Stop"

$DemoRoot = (Resolve-Path "$PSScriptRoot\..").Path
if ([string]::IsNullOrWhiteSpace($Config)) {
    $Config = Join-Path $DemoRoot "build\wau_de0_nano_cw_stress_last.json"
}
if ([string]::IsNullOrWhiteSpace($Report)) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $Report = Join-Path $DemoRoot "build\cw_stress_benchmark_${stamp}.json"
}

$env:PYTHONPATH = (Join-Path $DemoRoot "host")

& python `
    (Join-Path $DemoRoot "host\programs\run_cw_stress_benchmark.py") `
    --host $ServerHost `
    --port $Port `
    --flow-id $FlowId `
    --random-iters $RandomIters `
    --random-range $RandomRange `
    --seed $Seed `
    --config $Config `
    --report $Report

if ($LASTEXITCODE -ne 0) {
    throw "run_cw_stress_benchmark.py failed (exit $LASTEXITCODE)"
}
