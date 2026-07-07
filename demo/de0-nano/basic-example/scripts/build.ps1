# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Generate WAU RTL from the demo config and run the Quartus full compile.
#
# Usage:
#   .\scripts\build.ps1                 # base config, no .cw merge
#   .\scripts\build.ps1 -WithCw         # also compile-cw merge the basic_arithmetic.cw
#   .\scripts\build.ps1 -SkipGen        # skip RTL gen (e.g. already up to date)
#   .\scripts\build.ps1 -SkipQuartus    # only regenerate RTL, don't compile
[CmdletBinding()]
param(
    [switch] $WithCw,
    [switch] $SkipGen,
    [switch] $SkipQuartus,
    [string] $QuartusRoot = "C:\altera_standard\25.1std"
)

$ErrorActionPreference = "Stop"

# Repo root: this script is at demo/de0-nano/basic-example/scripts/build.ps1
$DemoRoot   = (Resolve-Path "$PSScriptRoot\..").Path
$RepoRoot   = (Resolve-Path "$DemoRoot\..\..\..").Path
$QuartusDir = Join-Path $DemoRoot "quartus"
$WauRtlDir  = Join-Path $QuartusDir "wau_rtl"
$GenOutDir  = Join-Path $DemoRoot "build\generated"
$BaseCfg    = Join-Path $DemoRoot "host\config\wau_de0_nano_basic.json"
$MergedCfg  = Join-Path $DemoRoot "build\wau_de0_nano_basic_merged.json"
$CwProgram  = Join-Path $RepoRoot "CWs\basic_arithmetic.cw"

Write-Host "Repo root  : $RepoRoot"
Write-Host "Demo root  : $DemoRoot"
Write-Host "Quartus    : $QuartusRoot"

# -----------------------------------------------------------------------------
# 1. RTL generation
# -----------------------------------------------------------------------------
if (-not $SkipGen) {
    $env:PYTHONPATH = (Join-Path $RepoRoot "src\python")

    $cfgToUse = $BaseCfg

    if ($WithCw) {
        Write-Host "`n[1/3] compile-cw: merging $CwProgram into base config ..." -ForegroundColor Cyan
        New-Item -ItemType Directory -Force -Path (Split-Path $MergedCfg) | Out-Null
        & python -m waugen compile-cw `
            --program-file $CwProgram `
            --flow-id 90 `
            --name cw_basic_arithmetic `
            --entry "0,0" `
            --base-config $BaseCfg `
            --out-config $MergedCfg `
            --replace-existing `
            --program-id 90 `
            --program-name cw_basic_arithmetic_program `
            --program-priority 3 `
            --program-replicas 1
        if ($LASTEXITCODE -ne 0) { throw "compile-cw failed (exit $LASTEXITCODE)" }
        $cfgToUse = $MergedCfg
    } else {
        Write-Host "`n[1/3] (skipping compile-cw; pass -WithCw to merge $CwProgram)" -ForegroundColor DarkGray
    }

    Write-Host "`n[2/3] generate RTL from $cfgToUse" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $GenOutDir | Out-Null
    & python -m waugen generate --config $cfgToUse --out $GenOutDir --summary
    if ($LASTEXITCODE -ne 0) { throw "waugen generate failed (exit $LASTEXITCODE)" }

    Write-Host "  syncing generated RTL into $WauRtlDir"
    New-Item -ItemType Directory -Force -Path $WauRtlDir | Out-Null
    # Mirror the files the Quartus project's QSF expects.
    $needed = @(
        "wau_defs.vh",
        "wau_operation_alu.v",
        "wau_neighbor_forward.v",
        "wau_highway_router.v",
        "wau_highway_mesh.v",
        "wau_core_station.v",
        "wau_core.v",
        "wau_coordinator.v",
        "wau_host_mmio.v",
        "wau_top.v"
    )
    foreach ($f in $needed) {
        $src = Join-Path $GenOutDir $f
        if (-not (Test-Path $src)) { throw "Generator did not produce $f at $src" }
        Copy-Item -Force $src (Join-Path $WauRtlDir $f)
    }
} else {
    Write-Host "[1/3] -SkipGen: leaving wau_rtl/ as-is" -ForegroundColor DarkGray
}

# -----------------------------------------------------------------------------
# 2. Quartus build (quartus_sh -t flow.tcl pattern)
# -----------------------------------------------------------------------------
if ($SkipQuartus) {
    Write-Host "`n[3/3] -SkipQuartus: not compiling. Generated RTL is up to date." -ForegroundColor DarkGray
    exit 0
}

$quartus_sh = Join-Path $QuartusRoot "quartus\bin64\quartus_sh.exe"
if (-not (Test-Path $quartus_sh)) {
    throw "quartus_sh.exe not found at $quartus_sh. Pass -QuartusRoot."
}

# Auto-create a minimal flow.tcl that runs map/fit/asm/sta.
$FlowTcl = Join-Path $DemoRoot "build\flow.tcl"
@"
load_package flow
project_open -force -revision wau_de0_nano_basic wau_de0_nano_basic
execute_flow -compile
project_close
"@ | Set-Content -Encoding ASCII $FlowTcl

Push-Location $QuartusDir
try {
    Write-Host "`n[3/3] quartus_sh -t $FlowTcl" -ForegroundColor Cyan
    & $quartus_sh -t $FlowTcl
    if ($LASTEXITCODE -ne 0) { throw "quartus_sh execute_flow -compile failed (exit $LASTEXITCODE)" }

    $sof = Join-Path $QuartusDir "output_files\wau_de0_nano_basic.sof"
    if (Test-Path $sof) {
        Write-Host "`nBuild OK: $sof" -ForegroundColor Green
    } else {
        Write-Host "Build completed but $sof was not produced." -ForegroundColor Yellow
    }
}
finally {
    Pop-Location
}
