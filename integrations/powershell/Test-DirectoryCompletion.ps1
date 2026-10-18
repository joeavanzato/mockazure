$ErrorActionPreference='Stop'
. /project/integrations/powershell/Connect-MockAzure.ps1
Connect-MockAzure -ConfigPath /state/client.json
Import-Module Microsoft.Graph.DirectoryObjects -RequiredVersion 2.35.1
Import-Module Microsoft.Graph.Groups -RequiredVersion 2.35.1
Import-Module Microsoft.Graph.Identity.Governance -RequiredVersion 2.35.1
$config=Get-Content /state/client.json -Raw | ConvertFrom-Json
$ids=@($config.user_id)+@($config.group_ids)
$found=@(Get-MgDirectoryObjectById -BodyParameter @{ids=$ids})
if($found.Count -ne 4 -or @(Compare-Object ($ids | Sort-Object) ($found.Id | Sort-Object)).Count){throw 'Mixed directory resolution mismatch'}
$item=Get-MgDirectoryObject -DirectoryObjectId $config.user_id
if($item.Id -ne $config.user_id){throw 'Item resolution mismatch'}
$owned=@(Get-MgGroupMemberOf -GroupId $config.leaf_id -All -PageSize 1)
if($owned.Count -ne 3 -or @(Compare-Object ($config.group_ids | Sort-Object) ($owned.Id | Sort-Object)).Count){throw 'Membership paging mismatch'}
$org=Get-MgOrganization -Property id,city
if($org.City -ne 'Qualification City'){throw 'Typed organization property missing'}
$role=Get-MgRoleManagementDirectoryRoleDefinition -UnifiedRoleDefinitionId fe930be7-5e62-47db-91af-98c3a49a38b1
if(-not $role.Description -or -not $role.RolePermissions[0].AllowedResourceActions.Count){throw 'Typed role permissions missing'}
@{client='Microsoft Graph PowerShell';version='2.35.1';mixed_objects=$found.Count;membership_rows=$owned.Count;page_size=1;typed_organization=$true;typed_role_permissions=$true} | ConvertTo-Json | Set-Content /state/powershell-report.json
Disconnect-MgGraph | Out-Null
