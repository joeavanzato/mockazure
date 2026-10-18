$ErrorActionPreference='Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
Connect-MockAzure -ConfigPath /state/client.json -Mode AuthorizationCode -TokenFile /state/delegated-token.json
Import-Module Microsoft.Graph.Identity.SignIns -RequiredVersion 2.35.1
$config=Get-Content /state/client.json -Raw | ConvertFrom-Json
. /project/integrations/powershell/Graph-LocalTransport.ps1 -LocalOrigin $config.endpoints.graph
$root=Get-MgPolicyAuthenticationMethodPolicy
if($root.Id -ne 'authenticationMethodsPolicy'){throw 'Policy identity mismatch'}
$configs=@(Get-MgPolicyAuthenticationMethodPolicyAuthenticationMethodConfiguration)
if($configs.Count -ne 2){throw 'Policy configuration collection mismatch'}
Update-MgPolicyAuthenticationMethodPolicyAuthenticationMethodConfiguration -AuthenticationMethodConfigurationId Sms -BodyParameter @{'@odata.type'='#microsoft.graph.smsAuthenticationMethodConfiguration';state='disabled'}
$read=Get-MgPolicyAuthenticationMethodPolicyAuthenticationMethodConfiguration -AuthenticationMethodConfigurationId Sms
if($read.State -ne 'disabled'){throw 'Policy update missing'}
Update-MgPolicyAuthenticationMethodPolicyAuthenticationMethodConfiguration -AuthenticationMethodConfigurationId Sms -BodyParameter @{'@odata.type'='#microsoft.graph.smsAuthenticationMethodConfiguration';state='enabled';includeTargets=@(@{id='all_users';targetType='group';isUsableForSignIn=$false})}
Update-MgPolicyAuthenticationMethodPolicyAuthenticationMethodConfiguration -AuthenticationMethodConfigurationId MicrosoftAuthenticator -BodyParameter @{'@odata.type'='#microsoft.graph.microsoftAuthenticatorAuthenticationMethodConfiguration';state='enabled';includeTargets=@(@{id='all_users';targetType='group';authenticationMode='push'})}
@{client='Microsoft Graph PowerShell';version='2.35.1';policy_reads=3;method_policy_writes=3;delegated_policy_write=$true} | ConvertTo-Json | Set-Content /state/powershell-report.json
