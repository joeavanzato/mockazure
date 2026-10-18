param([ValidateSet("methods","reset")][string]$Phase="methods")
$ErrorActionPreference='Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
if($Phase -eq 'methods') { Connect-MockAzure -ConfigPath /state/client.json }
else { Connect-MockAzure -ConfigPath /state/client.json -Mode AuthorizationCode -TokenFile /state/delegated-token.json }
Import-Module Microsoft.Graph.Identity.SignIns -RequiredVersion 2.35.1
$config=Get-Content /state/client.json -Raw | ConvertFrom-Json
. /project/integrations/powershell/Graph-LocalTransport.ps1 -LocalOrigin $config.endpoints.graph
$user=$config.powershell_user_id
if($Phase -eq 'methods') {
$phone=New-MgUserAuthenticationPhoneMethod -UserId $user -BodyParameter @{phoneNumber='+1 5555550123';phoneType='mobile'}
if($phone.Id -ne '3179e48a-750b-4051-897c-87b9720928f7'){throw 'Phone identity mismatch'}
Update-MgUserAuthenticationPhoneMethod -UserId $user -PhoneAuthenticationMethodId $phone.Id -BodyParameter @{phoneNumber='+1 5555550124'}
$read=Get-MgUserAuthenticationPhoneMethod -UserId $user -PhoneAuthenticationMethodId $phone.Id
if($read.PhoneNumber -ne '+1 5555550124'){throw 'Phone update missing'}
Remove-MgUserAuthenticationPhoneMethod -UserId $user -PhoneAuthenticationMethodId $phone.Id -Confirm:$false
$email=New-MgUserAuthenticationEmailMethod -UserId $user -BodyParameter @{emailAddress='recover@example.test'}
Update-MgUserAuthenticationEmailMethod -UserId $user -EmailAuthenticationMethodId $email.Id -BodyParameter @{emailAddress='changed@example.test'}
$read=Get-MgUserAuthenticationEmailMethod -UserId $user -EmailAuthenticationMethodId $email.Id
if($read.EmailAddress -ne 'changed@example.test'){throw 'Email update missing'}
Remove-MgUserAuthenticationEmailMethod -UserId $user -EmailAuthenticationMethodId $email.Id -Confirm:$false
Remove-MgUserAuthenticationFido2Method -UserId $user -Fido2AuthenticationMethodId $config.powershell_fido_id -Confirm:$false
Remove-MgUserAuthenticationMicrosoftAuthenticatorMethod -UserId $user -MicrosoftAuthenticatorAuthenticationMethodId $config.powershell_authenticator_id -Confirm:$false
if(@(Get-MgUserAuthenticationMethod -UserId $user).Count -ne 1){throw 'Method delete missing'}
@{client='Microsoft Graph PowerShell';version='2.35.1';method_mutations=8} | ConvertTo-Json | Set-Content /state/powershell-report.json
return
}
Reset-MgUserAuthenticationMethodPassword -UserId $user -AuthenticationMethodId '28c10230-6103-485e-b985-444c60001490' -BodyParameter @{newPassword='Native temporary password 72!'} -ResponseHeadersVariable resetHeaders | Out-Null
$location=@($resetHeaders['Location'])[0]
if(-not $location.StartsWith($config.endpoints.graph)){throw 'Reset Location is not local'}
$operationId=$location.Split('/')[-1]
for($i=0;$i -lt 60;$i++) {
    $operation=Get-MgUserAuthenticationOperation -UserId $user -LongRunningOperationId $operationId
    if($operation.Status -in @('succeeded','failed')){break}
    Start-Sleep -Seconds 1
}
if($operation.Status -ne 'succeeded'){throw 'Password reset failed'}
@{client='Microsoft Graph PowerShell';version='2.35.1';method_mutations=8;reset_operation=$operationId;reset_status=$operation.Status} | ConvertTo-Json | Set-Content /state/powershell-report.json
