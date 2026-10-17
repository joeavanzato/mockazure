param([Parameter(Mandatory)][string]$SourceRoot,[Parameter(Mandatory)][string]$OutputPath)
$ErrorActionPreference='Stop'
$files=[Collections.Generic.List[object]]::new()
foreach($file in Get-ChildItem -LiteralPath $SourceRoot -Recurse -File | Where-Object Extension -in '.ps1','.psm1'){
    $tokens=$null;$errors=$null
    $ast=[System.Management.Automation.Language.Parser]::ParseFile($file.FullName,[ref]$tokens,[ref]$errors)
    if($errors.Count){throw "Cannot inventory PowerShell parse errors in $($file.Name)"}
    $calls=@($ast.FindAll({param($n) $n -is [System.Management.Automation.Language.CommandAst]},$true) | ForEach-Object {
        $name=$_.GetCommandName()
        if($name -match '^(Get|Search|Invoke|Export|New|Set|Remove|Update)-(Mg|Az|EXO|UnifiedAuditLog|Mailbox|InboxRule|TransportRule|AdminAuditLog|Msol|AzureAD|CASMailbox|MessageTrace|OrganizationConfig|Recipient|MobileDevice|AuditLog|ContentSearch|ComplianceSearch)'){
            @{command=$name;line=$_.Extent.StartLineNumber;parameters=@($_.CommandElements | Where-Object {$_ -is [System.Management.Automation.Language.CommandParameterAst]} | ForEach-Object ParameterName)}
        }
    })
    $urls=@($ast.FindAll({param($n) $n -is [System.Management.Automation.Language.StringConstantExpressionAst] -or $n -is [System.Management.Automation.Language.ExpandableStringExpressionAst]},$true) | Where-Object Value -match '(graph\.microsoft\.com|management\.azure\.com|outlook\.office365\.com|/(v1\.0|beta)/)' | ForEach-Object {
        @{template=$_.Value;line=$_.Extent.StartLineNumber}
    })
    $dynamic=@($ast.FindAll({param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $null -eq $n.GetCommandName()},$true) | ForEach-Object {$_.Extent.StartLineNumber})
    $definitions=@($ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst]},$true) | ForEach-Object Name)
    $files.Add(@{function_definitions=$definitions;path=[IO.Path]::GetRelativePath($SourceRoot,$file.FullName).Replace('\','/');sha256=(Get-FileHash $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant();calls=$calls;url_templates=$urls;dynamic_command_lines=$dynamic})
}
@{method='PowerShell AST named service calls and URL templates; dynamic dispatch is not resolved';files=@($files.ToArray())} | ConvertTo-Json -Depth 15 | Set-Content -LiteralPath $OutputPath -Encoding utf8
