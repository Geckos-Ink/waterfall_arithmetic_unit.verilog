# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.
#
# Windows wrapper around scripts/fetch_dataset.py: downloads a dataset (default
# MNIST) into the git-ignored datasets/ directory, skipping files already
# present. All arguments are forwarded to the Python script.
#
# Usage:
#   .\scripts\fetch_dataset.ps1
#   .\scripts\fetch_dataset.ps1 -Dataset mnist -Force
#   .\scripts\fetch_dataset.ps1 -DryRun
[CmdletBinding()]
param(
    [string] $Dataset = "mnist",
    [string] $Dest = "",
    [switch] $Force,
    [switch] $DryRun,
    [switch] $Quiet,
    [string] $Python = "python"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$Script = Join-Path $PSScriptRoot "fetch_dataset.py"

$cmdArgs = @($Script, "--dataset", $Dataset)
if (-not [string]::IsNullOrWhiteSpace($Dest)) { $cmdArgs += @("--dest", $Dest) }
if ($Force)  { $cmdArgs += "--force" }
if ($DryRun) { $cmdArgs += "--dry-run" }
if ($Quiet)  { $cmdArgs += "--quiet" }

Write-Host "[fetch_dataset.ps1] $Python $($cmdArgs -join ' ')"
& $Python @cmdArgs
if ($LASTEXITCODE -ne 0) {
    throw "fetch_dataset.py failed (exit $LASTEXITCODE)"
}
