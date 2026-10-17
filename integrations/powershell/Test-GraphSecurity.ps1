param([string]$ConfigPath = '/state/client.json')
$ErrorActionPreference = 'Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
foreach ($name in @('Microsoft.Graph.Applications','Microsoft.Graph.Identity.Governance',
    'Microsoft.Graph.Identity.DirectoryManagement','Microsoft.Graph.Identity.SignIns',
    'Microsoft.Graph.Users','Microsoft.Graph.Reports')) {
    Import-Module $name -RequiredVersion 2.35.1
}
function Assert-True($Value, $Message) { if (-not $Value) { throw $Message } }
function Assert-Denied([scriptblock]$Action, [int]$Status=403) {
    $denied=$false
    try { & $Action | Out-Null } catch {
        $code = switch ($Status) {
            403 {'Authorization_RequestDenied'}
            404 {'Request_ResourceNotFound'}
            401 {'invalid_client'}
            default {'__no_matching_code__'}
        }
        $denied=($_.Exception.Message -match "(?<![0-9])$Status(?![0-9])|$code")
        if ($_.Exception.Response) {
            $denied=([int]$_.Exception.Response.StatusCode -eq $Status)
        }
        if (-not $denied) { throw }
    }
    Assert-True $denied "Expected status $Status was not returned"
}
function Get-LocalToken($ClientId, $Secret) {
    Invoke-RestMethod -Method POST -Uri "$($config.endpoints.login)/$($config.tenant_id)/oauth2/v2.0/token" -Body @{
        grant_type='client_credentials';client_id=$ClientId;client_secret=$Secret
        scope='https://graph.microsoft.com/.default'
    }
}
$result=[ordered]@{passed=$false;client='Microsoft Graph PowerShell 2.35.1';modes=[ordered]@{}}
foreach($mode in @('application','delegated')) {
    if($mode -eq 'application'){Connect-MockAzure -ConfigPath $ConfigPath}
    else{Connect-MockAzure -ConfigPath $ConfigPath -Mode AuthorizationCode -TokenFile /state/delegated-token.json}
    $checks=[ordered]@{}
    try {
        $resource=Get-MgServicePrincipal -Filter "appId eq '00000003-0000-0000-c000-000000000000'"
        $orgRole=($resource.AppRoles | Where-Object Value -eq 'Organization.Read.All').Id
        $userRole=($resource.AppRoles | Where-Object Value -eq 'User.Read.All').Id
        $app=New-MgApplication -BodyParameter @{
            displayName="PowerShell qualification $mode"
            requiredResourceAccess=@(@{resourceAppId=$resource.AppId;resourceAccess=@(@{id=$orgRole;type='Role'})})
        }
        Assert-True ($app.Id -ne $app.AppId) 'Graph object ID aliases appId'
        Update-MgApplication -ApplicationId $app.Id -BodyParameter @{displayName="PowerShell updated $mode"}
        Assert-True ((Get-MgApplication -ApplicationId $app.Id).DisplayName -eq "PowerShell updated $mode") 'Application update missing'
        $sp=New-MgServicePrincipal -BodyParameter @{appId=$app.AppId}
        Update-MgServicePrincipal -ServicePrincipalId $sp.Id -BodyParameter @{displayName="PowerShell principal $mode"}
        Assert-True ((Get-MgServicePrincipal -ServicePrincipalId $sp.Id).DisplayName -eq "PowerShell principal $mode") 'SP update missing'
        New-MgApplicationOwnerByRef -ApplicationId $app.Id -BodyParameter @{
            '@odata.id'="$($config.endpoints.graph)/v1.0/directoryObjects/$($config.seed_first_id)"
        }
        Assert-True ((Get-MgApplicationOwner -ApplicationId $app.Id).Id -contains $config.seed_first_id) 'Owner missing'
        Remove-MgApplicationOwnerByRef -ApplicationId $app.Id -DirectoryObjectId $config.seed_first_id
        Assert-True (-not ((Get-MgApplicationOwner -ApplicationId $app.Id).Id -contains $config.seed_first_id)) 'Owner removal missing'
        $checks.owner_reference=$true
        $orgAssignment=New-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $sp.Id -BodyParameter @{
            principalId=$sp.Id;resourceId=$resource.Id;appRoleId=$orgRole
        }
        $password=Add-MgApplicationPassword -ApplicationId $app.Id -BodyParameter @{
            passwordCredential=@{displayName='PowerShell qualification secret'}
        }
        Assert-True ($password.SecretText -and $password.KeyId) 'Secret creation failed'
        $token=Get-LocalToken $app.AppId $password.SecretText
        $headers=@{Authorization="Bearer $($token.access_token)"}
        $organization=Invoke-RestMethod -Uri "$($config.endpoints.graph)/v1.0/organization" -Headers $headers
        Assert-True ($organization.value[0].id -eq $config.tenant_id) 'Created secret cannot authenticate'
        Assert-Denied {Invoke-RestMethod -Uri "$($config.endpoints.graph)/v1.0/users" -Headers $headers}
        $assignment=New-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $sp.Id -BodyParameter @{
            principalId=$sp.Id;resourceId=$resource.Id;appRoleId=$userRole
        }
        $token=Get-LocalToken $app.AppId $password.SecretText
        $users=Invoke-RestMethod -Uri "$($config.endpoints.graph)/v1.0/users" -Headers @{Authorization="Bearer $($token.access_token)"}
        Assert-True ($users.value.Count -gt 0) 'Application grant ineffective'
        Remove-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $sp.Id -AppRoleAssignmentId $assignment.Id
        $token=Get-LocalToken $app.AppId $password.SecretText
        Assert-Denied {Invoke-RestMethod -Uri "$($config.endpoints.graph)/v1.0/users" -Headers @{Authorization="Bearer $($token.access_token)"}}
        $checks.application_grant=$true
        $grant=New-MgOauth2PermissionGrant -BodyParameter @{
            clientId=$sp.Id;resourceId=$resource.Id;consentType='AllPrincipals';scope='User.Read'
        }
        Update-MgOauth2PermissionGrant -OAuth2PermissionGrantId $grant.Id -BodyParameter @{scope='User.Read Organization.Read.All'}
        Assert-True ((Get-MgOauth2PermissionGrant -OAuth2PermissionGrantId $grant.Id).Scope -match 'Organization.Read.All') 'Delegated grant update missing'
        Remove-MgOauth2PermissionGrant -OAuth2PermissionGrantId $grant.Id
        Assert-Denied {Get-MgOauth2PermissionGrant -OAuth2PermissionGrantId $grant.Id} 404
        $checks.delegated_grant=$true
        $role=New-MgRoleManagementDirectoryRoleAssignment -BodyParameter @{
            principalId=$sp.Id;roleDefinitionId='f2ef992c-3afb-46b9-b7cf-a126ee74c451';directoryScopeId='/'
        }
        Assert-True ((Get-MgRoleManagementDirectoryRoleAssignment -UnifiedRoleAssignmentId $role.Id).PrincipalId -eq $sp.Id) 'Role assignment missing'
        Remove-MgRoleManagementDirectoryRoleAssignment -UnifiedRoleAssignmentId $role.Id
        Assert-Denied {Get-MgRoleManagementDirectoryRoleAssignment -UnifiedRoleAssignmentId $role.Id} 404
        $checks.role_assignment=$true
        Remove-MgApplicationPassword -ApplicationId $app.Id -BodyParameter @{keyId=$password.KeyId}
        Assert-Denied {Get-LocalToken $app.AppId $password.SecretText} 401
        $metadata=(Get-MgApplication -ApplicationId $app.Id).PasswordCredentials
        Assert-True (-not ($metadata | Where-Object SecretText)) 'Secret leaked in metadata'
        $checks.credential_roundtrip=$true
        Remove-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $sp.Id -AppRoleAssignmentId $orgAssignment.Id
        Remove-MgServicePrincipal -ServicePrincipalId $sp.Id
        Assert-Denied {Get-MgServicePrincipal -ServicePrincipalId $sp.Id} 404
        $checks.service_principal_crud=$true
        Remove-MgApplication -ApplicationId $app.Id
        Assert-Denied {Get-MgApplication -ApplicationId $app.Id} 404
        $checks.application_crud=$true
        $devices=@(Get-MgDevice -All)
        $checks.device_read=($null -ne $devices)
        if($mode -eq 'application'){
            Assert-Denied {New-MgDevice -BodyParameter @{displayName='Denied device';accountEnabled=$true}}
        }else{
            $device=New-MgDevice -BodyParameter @{displayName='PowerShell device';accountEnabled=$true}
            Update-MgDevice -DeviceId $device.Id -BodyParameter @{displayName='PowerShell updated device'}
            Assert-True ((Get-MgDevice -DeviceId $device.Id).DisplayName -eq 'PowerShell updated device') 'Device update missing'
            Remove-MgDevice -DeviceId $device.Id
            Assert-Denied {Get-MgDevice -DeviceId $device.Id} 404
        }
        $checks.device_write_profile=$true
        $methods=@(Get-MgUserAuthenticationMethod -UserId $config.user_id)
        Assert-True ($methods.Id -contains '28c10230-6103-485e-b985-444c60001490' -and $methods.Count -ge 4) 'Persisted methods missing'
        $phone=Get-MgUserAuthenticationPhoneMethod -UserId $config.user_id
        Assert-True ($phone.PhoneNumber -eq '+1 5555550150') 'Phone descriptor differs'
        $checks.method_inventory=$true
        $upn=(Get-Content $config.user_file -Raw | ConvertFrom-Json).username
        $registration=Get-MgReportAuthenticationMethodUserRegistrationDetail -Filter "userPrincipalName eq '$upn'"
        Assert-True ($registration.Id -eq $config.user_id -and $registration.IsMfaRegistered -and -not $registration.IsMfaCapable) 'Registration report differs'
        $checks.registration_report=$true
        $activity=Get-MgUser -UserId $config.user_id -Property id,signInActivity
        Assert-True ($activity.Id -eq $config.user_id -and $null -ne $activity.SignInActivity) 'Sign-in activity unavailable'
        $checks.signin_activity=$true
        $location=New-MgIdentityConditionalAccessNamedLocation -BodyParameter @{
            '@odata.type'='#microsoft.graph.ipNamedLocation';displayName='PowerShell location';isTrusted=$true
            ipRanges=@(@{'@odata.type'='#microsoft.graph.iPv4CidrRange';cidrAddress='192.0.2.0/24'})
        }
        Update-MgIdentityConditionalAccessNamedLocation -NamedLocationId $location.Id -BodyParameter @{
            '@odata.type'='#microsoft.graph.ipNamedLocation';displayName='PowerShell updated location'
        }
        Assert-True ((Get-MgIdentityConditionalAccessNamedLocation -NamedLocationId $location.Id).DisplayName -eq 'PowerShell updated location') 'Location update missing'
        $policy=New-MgIdentityConditionalAccessPolicy -BodyParameter @{
            displayName='PowerShell policy';state='disabled'
            conditions=@{applications=@{includeApplications=@('All')};users=@{includeUsers=@('All')};clientAppTypes=@('all')}
            grantControls=@{operator='OR';builtInControls=@('mfa')}
        }
        Update-MgIdentityConditionalAccessPolicy -ConditionalAccessPolicyId $policy.Id -BodyParameter @{displayName='PowerShell updated policy'}
        Assert-True ((Get-MgIdentityConditionalAccessPolicy -ConditionalAccessPolicyId $policy.Id).DisplayName -eq 'PowerShell updated policy') 'Policy update missing'
        Remove-MgIdentityConditionalAccessPolicy -ConditionalAccessPolicyId $policy.Id
        Assert-Denied {Get-MgIdentityConditionalAccessPolicy -ConditionalAccessPolicyId $policy.Id} 404
        $checks.policy_crud=$true
        Remove-MgIdentityConditionalAccessNamedLocation -NamedLocationId $location.Id
        Assert-Denied {Get-MgIdentityConditionalAccessNamedLocation -NamedLocationId $location.Id} 404
        $checks.location_crud=$true
    }finally{Disconnect-MgGraph | Out-Null}
    $denied=Get-Content "/state/denied_$mode-token.json" -Raw | ConvertFrom-Json
    Connect-MgGraph -AccessToken (ConvertTo-SecureString $denied.access_token -AsPlainText -Force) -Environment MockAzure -NoWelcome
    try {
        Assert-Denied {Get-MgApplication -Top 1}
        Assert-Denied {Get-MgIdentityConditionalAccessPolicy}
        $checks.permission_denied=$true
    }finally{Disconnect-MgGraph | Out-Null}
    $result.modes[$mode]=$checks
}
$result.passed=$true
$result | ConvertTo-Json -Depth 10 | Set-Content $config.output_file
@{passed=$true;client=$result.client} | ConvertTo-Json
