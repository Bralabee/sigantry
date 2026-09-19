#Requires -Version 7.4
#Requires -Module @{ ModuleName = 'Pester'; ModuleVersion = '5.7.0' }

<#
.SYNOPSIS
    Shared Pester 5 configuration for the Fabric DataOps Toolkits repo.
.DESCRIPTION
    Returns a PesterConfiguration object with conventions:
      - Output: Minimal
      - CodeCoverage: Disabled (Phase 0 ships prerequisite scripts; coverage lands later)
      - Test discovery:
          tests/prereqs             (Phase 0)
          tests/Sigantry            (Phase 8 Plan 08-04 generic module tests; renamed from tests/Fabric in Plan 10-04 per ADR-0011)
          tests/SigantryHs2         (Phase 1; Plan 08-04 plugin-shape; renamed from tests/Hs2Fabric in Plan 10-04 per ADR-0011)
          tests/scripts/bootstrap   (Phase 0)
      - PSModulePath: the repo root is prepended so in-repo modules
        (Sigantry/, SigantryHs2/) resolve by name. SigantryHs2.psd1 declares
        RequiredModules=@{ModuleName='Sigantry'; ModuleVersion='3.0.0'},
        which the PowerShell module loader can only satisfy via
        PSModulePath-based resolution (not explicit-path imports).

    Phase 0 (Plan 02, commit 0effb0b) created this file for tests/prereqs.
    Phase 1 (Plan 02) extended Run.Path to include tests/Hs2Fabric.
    Phase 8 Plan 08-04 extended Run.Path to include tests/Fabric and
    prepended the repo root to PSModulePath.
    Phase 10 Plan 10-04 renamed tests/Fabric -> tests/Sigantry and
    tests/Hs2Fabric -> tests/SigantryHs2 (ADR-0011).
.EXAMPLE
    $config = & ./tests/Pester.config.ps1
    Invoke-Pester -Configuration $config
#>

Set-StrictMode -Version 3.0

# Prepend the repo root to PSModulePath so in-repo modules (Sigantry/,
# SigantryHs2/) resolve by name. Required for the SigantryHs2 manifest's
# RequiredModules=@{ModuleName='Sigantry';ModuleVersion='3.0.0'} pin
# (renamed from Fabric/Hs2Fabric in Plan 10-04 per ADR-0011).
$repoRoot = Split-Path -Parent $PSScriptRoot
if ($env:PSModulePath -notlike "*$repoRoot*") {
    $env:PSModulePath = $repoRoot + [System.IO.Path]::PathSeparator + $env:PSModulePath
}

$config = New-PesterConfiguration
$config.Run.Path = @(
    Join-Path $PSScriptRoot 'prereqs'
    Join-Path $PSScriptRoot 'Sigantry'
    Join-Path $PSScriptRoot 'SigantryHs2'
    Join-Path $PSScriptRoot 'scripts' 'bootstrap'
)
$config.Run.PassThru = $true
$config.Output.Verbosity = 'Minimal'
$config.TestResult.Enabled = $false
$config.CodeCoverage.Enabled = $false
$config.Should.ErrorAction = 'Stop'

return $config
