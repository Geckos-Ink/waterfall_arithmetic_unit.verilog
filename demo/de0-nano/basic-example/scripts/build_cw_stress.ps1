# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Build a DE0-Nano CW stress image from CWs/stress/mesh_stress.cw (or another
# compile-cw-compatible kernel) and optionally override the board grid.
#
# Usage:
#   .\scripts\build_cw_stress.ps1
#   .\scripts\build_cw_stress.ps1 -GridX 4 -GridY 4
#   .\scripts\build_cw_stress.ps1 -QuartusRoot C:\intelFPGA_lite\23.1std
[CmdletBinding()]
param(
    [int] $GridX = 4,
    [int] $GridY = 3,
    [int] $FlowId = 90,
    [int] $ProgramId = 90,
    [int] $LaneParallelism = 2,
    [int] $MaxInFlight = 2,
    [int] $ProgramReplicas = 2,
    [int] $ProgramMaxParallelFlows = 1,
    [int] $ProgramPriority = 4,
    [string] $ProgramLoadBalance = "least_busy",
    [string] $PlacementPolicy = "balance",
    [string] $LoweringProfile = "throughput_optimized",
    [string] $QuartusRoot = "C:\altera_standard\25.1std",
    [string] $ProgramFile = "",
    [string] $BaseConfig = "",
    [switch] $SkipGen,
    [switch] $SkipQuartus
)

$ErrorActionPreference = "Stop"

$DemoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$RepoRoot = (Resolve-Path "$DemoRoot\..\..\..").Path
$QuartusDir = Join-Path $DemoRoot "quartus"
$WauRtlDir = Join-Path $QuartusDir "wau_rtl"
$BuildDir = Join-Path $DemoRoot "build"

if ([string]::IsNullOrWhiteSpace($ProgramFile)) {
    $ProgramFile = (Join-Path $RepoRoot "CWs\stress\mesh_stress.cw")
}
if ([string]::IsNullOrWhiteSpace($BaseConfig)) {
    $BaseConfig = (Join-Path $DemoRoot "host\config\wau_de0_nano_cw_stress_base.json")
}

$ProgramFile = (Resolve-Path $ProgramFile).Path
$BaseConfig = (Resolve-Path $BaseConfig).Path

$Stem = "cw_stress_${GridX}x${GridY}"
$GridBaseCfg = Join-Path $BuildDir "${Stem}_base.json"
$MergedCfg = Join-Path $BuildDir "${Stem}_merged.json"
$GenOutDir = Join-Path $BuildDir "${Stem}_generated"
$FlowTcl = Join-Path $BuildDir "${Stem}_flow.tcl"
$ReportDir = Join-Path $BuildDir "${Stem}_quartus"
$LatestCfg = Join-Path $BuildDir "wau_de0_nano_cw_stress_last.json"

Write-Host "Repo root  : $RepoRoot"
Write-Host "Demo root  : $DemoRoot"
Write-Host "Quartus    : $QuartusRoot"
Write-Host "Program    : $ProgramFile"
Write-Host "Base config: $BaseConfig"
Write-Host "Grid       : ${GridX}x${GridY}"

if (-not $SkipGen) {
    $env:PYTHONPATH = (Join-Path $RepoRoot "src\python")
    New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

    Write-Host "`n[1/5] validate CW template" -ForegroundColor Cyan
    & python -m waugen cw-lint --program-file $ProgramFile --compile-template
    if ($LASTEXITCODE -ne 0) { throw "cw-lint failed (exit $LASTEXITCODE)" }

    Write-Host "`n[2/5] materialize board base config (${GridX}x${GridY})" -ForegroundColor Cyan
    $baseObj = Get-Content -Raw -Path $BaseConfig | ConvertFrom-Json
    $baseObj.device.grid.x = $GridX
    $baseObj.device.grid.y = $GridY
    $baseObj.project = "wau_de0_nano_cw_stress"
    $baseObj.output_module_name = "wau_top"
    $baseObj.flows = @()
    $baseObj.programs = @()
    $baseObj | ConvertTo-Json -Depth 100 | Set-Content -Encoding ASCII $GridBaseCfg

    Write-Host "`n[3/5] compile-cw merge" -ForegroundColor Cyan
    & python -m waugen compile-cw `
        --program-file $ProgramFile `
        --flow-id $FlowId `
        --name "cw_stress_flow_${GridX}x${GridY}" `
        --entry "0,0" `
        --max-in-flight $MaxInFlight `
        --lane-parallelism $LaneParallelism `
        --placement-policy $PlacementPolicy `
        --lowering-profile $LoweringProfile `
        --base-config $GridBaseCfg `
        --out-config $MergedCfg `
        --replace-existing `
        --program-id $ProgramId `
        --program-name "cw_stress_program_${GridX}x${GridY}" `
        --program-priority $ProgramPriority `
        --program-replicas $ProgramReplicas `
        --program-max-parallel-flows $ProgramMaxParallelFlows `
        --program-load-balance $ProgramLoadBalance
    if ($LASTEXITCODE -ne 0) { throw "compile-cw failed (exit $LASTEXITCODE)" }

    Write-Host "`n[4/5] validate merged config" -ForegroundColor Cyan
    & python -m waugen validate --config $MergedCfg
    if ($LASTEXITCODE -ne 0) { throw "waugen validate failed (exit $LASTEXITCODE)" }

    Write-Host "`n[5/5] generate RTL" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $GenOutDir | Out-Null
    & python -m waugen generate --config $MergedCfg --out $GenOutDir --summary
    if ($LASTEXITCODE -ne 0) { throw "waugen generate failed (exit $LASTEXITCODE)" }

    Copy-Item -Force $MergedCfg $LatestCfg

    Write-Host "  syncing generated RTL into $WauRtlDir"
    New-Item -ItemType Directory -Force -Path $WauRtlDir | Out-Null
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
    Write-Host "[1/5] -SkipGen: leaving merged config / wau_rtl as-is" -ForegroundColor DarkGray
}

if ($SkipQuartus) {
    Write-Host "`nQuartus compile skipped." -ForegroundColor DarkGray
    exit 0
}

$quartus_sh = Join-Path $QuartusRoot "quartus\bin64\quartus_sh.exe"
if (-not (Test-Path $quartus_sh)) {
    throw "quartus_sh.exe not found at $quartus_sh. Pass -QuartusRoot."
}

Write-Host "`n[quartus] cleaning previous Quartus work dirs" -ForegroundColor Cyan
$cleanupTargets = @(
    (Join-Path $QuartusDir "db"),
    (Join-Path $QuartusDir "incremental_db"),
    (Join-Path $QuartusDir "output_files")
)
foreach ($target in $cleanupTargets) {
    if (Test-Path $target) {
        Remove-Item -Recurse -Force -LiteralPath $target
    }
}

@"
load_package flow
project_open -force -revision wau_de0_nano_basic wau_de0_nano_basic
execute_flow -compile
project_close
"@ | Set-Content -Encoding ASCII $FlowTcl

Push-Location $QuartusDir
try {
    Write-Host "`n[quartus] quartus_sh -t $FlowTcl" -ForegroundColor Cyan
    # Quartus 25.1's quartus_sh prints a harmless "TBBmalloc: skip allocation..."
    # line to stderr at startup. Under ErrorActionPreference=Stop, PowerShell 5.1
    # turns that native stderr into a terminating error and aborts before the
    # compile runs, so relax the preference around the native call and rely on the
    # real process exit code instead.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $quartus_sh -t $FlowTcl
    $quartusExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($quartusExit -ne 0) { throw "quartus_sh execute_flow -compile failed (exit $quartusExit)" }
}
finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
$artifacts = @(
    "wau_de0_nano_basic.sof",
    "wau_de0_nano_basic.flow.rpt",
    "wau_de0_nano_basic.fit.summary",
    "wau_de0_nano_basic.sta.summary",
    "wau_de0_nano_basic.pin",
    "wau_de0_nano_basic.qsf",
    "wau_de0_nano_basic.done"
)
foreach ($name in $artifacts) {
    $src = Join-Path $QuartusDir $name
    if (Test-Path $src) {
        Copy-Item -Force $src (Join-Path $ReportDir $name)
    }
}

$sof = Join-Path $QuartusDir "wau_de0_nano_basic.sof"
if (Test-Path $sof) {
    Write-Host "`nBuild OK: $sof" -ForegroundColor Green
    Write-Host "Archived reports: $ReportDir" -ForegroundColor Green
} else {
    Write-Host "Build completed but $sof was not produced." -ForegroundColor Yellow
}
