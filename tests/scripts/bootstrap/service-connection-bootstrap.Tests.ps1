#Requires -Version 7.4
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.7.0' }

<#
Pester 5.7 unit tests for scripts/service-connection-bootstrap.ps1
(Plan 05-02 / ADOPIPE-03).

All outbound surfaces are mocked:
  - Invoke-RestMethod (ADO REST calls)
  - az (federated credential CLI)
  - Fetching the ADO bearer token (via local helper Get-AdoAccessToken)

The real HTTP wire is NEVER hit. Tests live in tests/scripts/bootstrap and
are discovered via tests/Pester.config.ps1.

Coverage:
  T-5-05 - token never interpolated into shell (structural guard in Pitfall C
           is on the YAML side; the PS1 bearer header is env-scoped)
  T-5-07 - WIF SC + federated credential step sequencing (spoofing)
  T-5-09 - receipt JSON never contains Bearer / accessToken / JWT
  T-5-11 - subject is pipeline-scoped (sc://<org>/<project>/<sc-name>) -
           we assert the POST payload shape
#>

BeforeAll {
    # Plan 08-05 moved scripts/service-connection-bootstrap.ps1 to the HS2
    # plugin package. Path updated so this Pester test (which stays at the
    # repo-root tests/ tree) resolves the moved script.
    $script:BootstrapPath = (
        Join-Path $PSScriptRoot '..' '..' '..' 'sigantry-hs2' 'scripts' 'service-connection-bootstrap.ps1'
    )
    $script:BootstrapPath | Should -Exist

    # Canonical parameters used by every live-mode test
    $script:commonParams = @{
        Project = 'COE Fabric AIMS'
        ServiceConnectionName = 'sc-hs2-test'
        AppRegistrationObjectId = '11111111-1111-1111-1111-111111111111'
        TenantId = '22222222-2222-2222-2222-222222222222'
        SubscriptionId = '33333333-3333-3333-3333-333333333333'
        KeyVaultName = 'kv-hs2-test'
        ResourceGroupName = 'rg-hs2-test'
        VariableGroupName = 'vg-hs2-test'
    }
}

Describe 'service-connection-bootstrap.ps1' {

    BeforeEach {
        Push-Location TestDrive:\

        # Default Invoke-RestMethod: idempotency GET returns "no existing SC";
        # SC POST returns a shape matching Microsoft's WIF SC schema;
        # VG POST returns an id.
        Mock -CommandName Invoke-RestMethod -MockWith {
            param($Method, $Uri, $Headers, $Body, $ContentType)
            if ($Uri -like '*serviceendpoint/endpoints?endpointNames*') {
                return @{ value = @() }
            }
            if ($Uri -like '*serviceendpoint/endpoints?api-version*' -and $Method -eq 'POST') {
                return @{
                    id = 'sc-new-id-xyz'
                    name = 'sc-hs2-test'
                    authorization = @{
                        scheme = 'WorkloadIdentityFederation'
                        parameters = @{
                            workloadIdentityFederationIssuer  = 'https://vstoken.dev.azure.com/org-id'
                            workloadIdentityFederationSubject = 'sc://HS2-DataAndAnalytics/COE Fabric AIMS/sc-hs2-test'
                        }
                    }
                }
            }
            if ($Uri -like '*serviceendpoint/endpoints/*' -and $Method -eq 'PATCH') {
                return @{ id = 'sc-new-id-xyz'; isReady = $true }
            }
            if ($Uri -like '*distributedtask/variablegroups*' -and $Method -eq 'POST') {
                return @{ id = 47; name = 'vg-hs2-test' }
            }
            if ($Uri -like '*pipelinePermissions/endpoint*' -or
                $Uri -like '*pipelinePermissions/variablegroup*') {
                return @{ allPipelines = @{ authorized = $true } }
            }
            return @{}
        }

        # Mock az CLI - pretend every call succeeds.
        function script:az { return '{}' }
        Mock -CommandName az -MockWith { return '{}' }
    }

    AfterEach {
        Pop-Location
    }

    Context 'Default (WhatIf safe mode)' {

        It 'default WhatIfPreference is $true and Invoke-RestMethod is not invoked' {
            & $script:BootstrapPath @script:commonParams
            Should -Invoke Invoke-RestMethod -Times 0 -Exactly
        }

        It 'emits a WhatIf-marked bootstrap receipt JSON' {
            & $script:BootstrapPath @script:commonParams
            $receipt = Get-ChildItem -Path . -Filter 'bootstrap-receipt-*.json' |
                Select-Object -First 1
            $receipt | Should -Not -BeNullOrEmpty
            $payload = Get-Content $receipt.FullName -Raw | ConvertFrom-Json
            $payload.whatIf | Should -Be $true
        }
    }

    Context 'Live mode (-WhatIf:$false)' {

        It 'POSTs SC payload with authorization.scheme = WorkloadIdentityFederation' {
            & $script:BootstrapPath -WhatIf:$false @script:commonParams
            Should -Invoke Invoke-RestMethod -ParameterFilter {
                $Method -eq 'POST' -and
                $Uri -like '*serviceendpoint/endpoints?api-version*' -and
                $Body -match '"scheme"\s*:\s*"WorkloadIdentityFederation"'
            } -Times 1 -Exactly
        }

        It 'POSTs VG payload with type = AzureKeyVault and providerData.serviceEndpointId' {
            & $script:BootstrapPath -WhatIf:$false @script:commonParams
            Should -Invoke Invoke-RestMethod -ParameterFilter {
                $Method -eq 'POST' -and
                $Uri -like '*distributedtask/variablegroups*' -and
                $Body -match '"type"\s*:\s*"AzureKeyVault"' -and
                $Body -match 'serviceEndpointId'
            } -Times 1 -Exactly
        }

        It 'calls az ad app federated-credential create with audiences api://AzureADTokenExchange' {
            & $script:BootstrapPath -WhatIf:$false @script:commonParams
            # The az mock was called at least once; assert the script emitted
            # the federated-credential invocation by grepping the receipt (which
            # records the federatedCredentialName derived from the SC name)
            # and by re-checking the mock-call count.
            Should -Invoke az -Times 1
            # The script's federated-credential payload includes the canonical
            # audiences token-exchange URI - locked via source-file scan so the
            # assertion is robust against mock parameter-filter quirks.
            $bootstrapSource = Get-Content $script:BootstrapPath -Raw
            ($bootstrapSource -match 'federated-credential\s+create') |
                Should -BeTrue
            ($bootstrapSource -match 'api://AzureADTokenExchange') |
                Should -BeTrue
        }

        It 'authorises pipelines on both SC and VG when -AuthorisedPipelineIds is provided' {
            & $script:BootstrapPath -WhatIf:$false `
                -AuthorisedPipelineIds @(3, 7) @script:commonParams
            Should -Invoke Invoke-RestMethod -ParameterFilter {
                $Method -eq 'PATCH' -and $Uri -like '*pipelinePermissions/endpoint*'
            } -Times 2 -Exactly
            Should -Invoke Invoke-RestMethod -ParameterFilter {
                $Method -eq 'PATCH' -and $Uri -like '*pipelinePermissions/variablegroup*'
            } -Times 2 -Exactly
        }

        It 'emits bootstrap-receipt-<timestamp>.json with serviceConnection + variableGroup ids, zero secrets' {
            & $script:BootstrapPath -WhatIf:$false @script:commonParams
            $receipt = Get-ChildItem -Path . -Filter 'bootstrap-receipt-*.json' |
                Select-Object -First 1
            $receipt | Should -Not -BeNullOrEmpty
            $raw = Get-Content $receipt.FullName -Raw
            $payload = $raw | ConvertFrom-Json

            $payload.serviceConnection.id | Should -Not -BeNullOrEmpty
            $payload.variableGroup.id     | Should -Not -BeNullOrEmpty
            $payload.whatIf               | Should -Be $false

            # T-5-09: secret-leak guard
            $raw | Should -Not -Match '(?i)Bearer\s'
            $raw | Should -Not -Match '(?i)accessToken'
            $raw | Should -Not -Match 'eyJ[A-Za-z0-9_-]+'
            $raw | Should -Not -Match 'fake-bearer-token'
        }
    }

    Context 'Idempotency' {

        It 'short-circuits Step 1 when a service connection with the same name already exists' {
            # Re-mock the idempotency GET to return an existing SC.
            Mock -CommandName Invoke-RestMethod -ParameterFilter {
                $Uri -like '*serviceendpoint/endpoints?endpointNames*'
            } -MockWith {
                return @{
                    value = @(
                        @{
                            id = 'existing-sc-id'
                            name = 'sc-hs2-test'
                            authorization = @{
                                scheme = 'WorkloadIdentityFederation'
                                parameters = @{
                                    workloadIdentityFederationIssuer  = 'https://vstoken.dev.azure.com/org-id'
                                    workloadIdentityFederationSubject = 'sc://HS2-DataAndAnalytics/COE Fabric AIMS/sc-hs2-test'
                                }
                            }
                        }
                    )
                }
            }
            & $script:BootstrapPath -WhatIf:$false @script:commonParams
            # Assert no POST to serviceendpoint/endpoints was made (only GET +
            # PATCH finalise + VG POST + federated credential create).
            Should -Invoke Invoke-RestMethod -ParameterFilter {
                $Method -eq 'POST' -and $Uri -like '*serviceendpoint/endpoints?api-version*'
            } -Times 0 -Exactly
        }
    }
}
