#Requires -Version 7.4
#Requires -Module @{ ModuleName = 'Pester'; ModuleVersion = '5.7.0' }

<#
.SYNOPSIS
    Shared Pester 5 configuration for the Sigantry repo.
.DESCRIPTION
    Returns a PesterConfiguration object with conventions:
      - Output: Minimal
      - CodeCoverage: Disabled (Phase 0 ships prerequisite scripts; coverage lands later)
      - Test discovery:
          tests/prereqs             (Phase 0)
          tests/Sigantry            (Phase 8 Plan 08-04 generic module tests; renamed from tests/Fabric in Plan 10-04 per ADR-0011)
      - PSModulePath: the repo root is prepended, so a PowerShell module
        kept at the repo root would resolve by name (a manifest's
        RequiredModules pin is satisfied only through PSModulePath, not
        through an explicit-path import).

    No PowerShell module and no *.Tests.ps1 file ship in this repository
    today, so a run reports that no test files were found. The file is
    kept because the Azure DevOps pester job (templates/stages/ci.yml,
    templates/jobs/build-powershell.yml) and the contributor docs call it.

    Phase 0 (Plan 02, commit 0effb0b) created this file for tests/prereqs.
    Phase 8 Plan 08-04 extended Run.Path to include tests/Fabric and
    prepended the repo root to PSModulePath.
    Phase 10 Plan 10-04 renamed tests/Fabric -> tests/Sigantry (ADR-0011).
.EXAMPLE
    $config = & ./tests/Pester.config.ps1
    Invoke-Pester -Configuration $config
#>

Set-StrictMode -Version 3.0

# Prepend the repo root to PSModulePath so a module kept at the repo root
# resolves by name, which a manifest's RequiredModules pin needs.
$repoRoot = Split-Path -Parent $PSScriptRoot
if ($env:PSModulePath -notlike "*$repoRoot*") {
    $env:PSModulePath = $repoRoot + [System.IO.Path]::PathSeparator + $env:PSModulePath
}

$config = New-PesterConfiguration
$config.Run.Path = @(
    Join-Path $PSScriptRoot 'prereqs'
    Join-Path $PSScriptRoot 'Sigantry'
)
$config.Run.PassThru = $true
$config.Output.Verbosity = 'Minimal'
$config.TestResult.Enabled = $false
$config.CodeCoverage.Enabled = $false
$config.Should.ErrorAction = 'Stop'

return $config
