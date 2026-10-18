"""Fixed server-generated command proxies; compiled EXO cmdlets stay in Microsoft's module."""

import re
from urllib.parse import urlsplit

from mockazure.endpoints import local_origin

RULE_PARAMETERS = {
    "Mailbox": "string",
    "Identity": "string",
    "Name": "string",
    "Priority": "int",
    "ForwardTo": "string[]",
    "RedirectTo": "string[]",
    "ForwardAsAttachmentTo": "string[]",
    "SubjectContainsWords": "string[]",
    "BodyContainsWords": "string[]",
    "SubjectOrBodyContainsWords": "string[]",
    "HeaderContainsWords": "string[]",
    "From": "string[]",
    "SentTo": "string[]",
    "MoveToFolder": "string",
    "CopyToFolder": "string",
    "DeleteMessage": "bool",
    "MarkAsRead": "bool",
    "StopProcessingRules": "bool",
    "MarkImportance": "string",
    "HasAttachment": "bool",
}
SWEEP_PARAMETERS = {
    "Mailbox": "string",
    "Identity": "string",
    "Name": "string",
    "Provider": "string",
    "Sender": "string",
    "SourceFolder": "string",
    "DestinationFolder": "string",
    "Enabled": "bool",
    "KeepForDays": "int",
    "KeepLatest": "bool",
    "ExceptIfFlagged": "bool",
    "ExceptIfPinned": "bool",
}
PARAMETERS = {
    "Get-OrganizationConfig": {},
    "Get-TransportConfig": {"Identity": "string"},
    "Get-AcceptedDomain": {"Identity": "string", "ResultSize": "string"},
    "Get-TransportRule": {"Identity": "string", "State": "string", "ResultSize": "string"},
    "Get-MailboxPermission": {"Identity": "string", "User": "string", "ResultSize": "string"},
    "Get-RecipientPermission": {
        "Identity": "string",
        "Trustee": "string",
        "AccessRights": "string[]",
        "ResultSize": "string",
    },
    "Get-Mailbox": {
        "Identity": "string",
        "ResultSize": "string",
        "RecipientTypeDetails": "string[]",
    },
    "Set-Mailbox": {
        "Identity": "string",
        "ForwardingAddress": "string",
        "ForwardingSmtpAddress": "string",
        "DeliverToMailboxAndForward": "bool",
    },
    "Get-InboxRule": {
        "Mailbox": "string",
        "Identity": "string",
        "IncludeHidden": "switch",
        "ResultSize": "string",
    },
    "New-InboxRule": {k: v for k, v in RULE_PARAMETERS.items() if k != "Identity"},
    "Set-InboxRule": RULE_PARAMETERS,
    "Remove-InboxRule": {"Mailbox": "string", "Identity": "string"},
    "Enable-InboxRule": {"Mailbox": "string", "Identity": "string"},
    "Disable-InboxRule": {"Mailbox": "string", "Identity": "string"},
    "Get-SweepRule": {"Mailbox": "string", "Identity": "string", "ResultSize": "string"},
    "New-SweepRule": {k: v for k, v in SWEEP_PARAMETERS.items() if k != "Identity"},
    "Set-SweepRule": SWEEP_PARAMETERS,
    "Remove-SweepRule": {"Mailbox": "string", "Identity": "string"},
}

PARAMETERS.update(
    {
        "Get-MessageTraceV2": {
            "EndDate": "datetime",
            "FromIP": "string",
            "MessageId": "string[]",
            "MessageTraceId": "guid",
            "RecipientAddress": "string[]",
            "ResultSize": "int",
            "SenderAddress": "string[]",
            "StartDate": "datetime",
            "StartingRecipientAddress": "string",
            "Status": "string[]",
            "SubjectFilterType": "string",
            "Subject": "string",
            "ToIP": "string",
        },
        "Get-MessageTraceDetailV2": {
            "MessageTraceId": "guid",
            "RecipientAddress": "string",
            "Action": "string[]",
            "EndDate": "datetime",
            "Event": "string[]",
            "MessageId": "string",
            "SenderAddress": "string",
            "StartDate": "datetime",
        },
    }
)


def fixed_proxy_source(command_names: tuple[str, ...], endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    local_origin(f"{parsed.scheme}://{parsed.netloc}")
    if (
        parsed.query
        or parsed.fragment
        or re.fullmatch(r"/adminapi/v2.0/[0-9a-f-]{36}/InvokeCommand", parsed.path) is None
    ):
        raise ValueError("Invalid local command endpoint")
    if not command_names or len(command_names) != len(set(command_names)):
        raise ValueError("Invalid command list")
    pieces = []
    for command in command_names:
        if command not in PARAMETERS:
            raise ValueError("Unsupported proxy command")
        parameters = PARAMETERS[command]
        nullable = {"ForwardingAddress", "ForwardingSmtpAddress"}
        declarations = ", ".join(
            f"[{'object' if name in nullable else kind}]\x24{name}"
            for name, kind in parameters.items()
        )
        writes = not command.startswith("Get-")
        binding = "CmdletBinding(SupportsShouldProcess=\x24true)" if writes else "CmdletBinding()"
        guard = (
            "    if (-not \x24PSCmdlet.ShouldProcess('local mailbox configuration', '"
            + command
            + "')) { return }\n"
            if writes
            else ""
        )

        recipient_projection = (
            "    foreach ($row in $result.value) {\n"
            "        foreach ($field in @('ForwardTo','RedirectTo','ForwardAsAttachmentTo',"
            "'From','SentTo')) {\n"
            "            foreach ($recipient in $row.$field) {\n"
            "                $recipient | Add-Member -MemberType ScriptMethod -Name ToString "
            "-Value { [string]$this.Address } -Force\n"
            "            }\n        }\n    }\n"
            if "InboxRule" in command
            else ""
        )
        pieces.append(
            f"function {command} {{\n    [{binding}] param({declarations})\n"
            + guard
            + "    $context = "
            "[Microsoft.Exchange.Management.ExoPowershellSnapin.ConnectionContextFactory]"
            "::GetCurrentConnectionContext($script:ConnectionContextObjectId)\n"
            + "    $auth = $context.GetAuthHeader()\n"
            + "    $parameters = @{}\n"
            + "    foreach ($entry in $PSBoundParameters.GetEnumerator()) {\n"
            + "        if ($entry.Key -in @("
            + ",".join("'" + k + "'" for k in parameters)
            + ")) {\n"
            + "            $value = $entry.Value\n"
            + "            if ($value -is [System.Management.Automation.SwitchParameter]) "
            "{ $value = [bool]$value }\n" + "            if ($value -is [datetime]) "
            "{ $value = $value.ToUniversalTime().ToString('o') }\n"
            + "            $parameters[$entry.Key] = $value\n        }\n    }\n"
            + f"    $body = @{{ CmdletInput = @{{ CmdletName = '{command}'; "
            "Parameters = $parameters } } | ConvertTo-Json -Depth 16\n"
            + f"    $result = Invoke-RestMethod -Method POST -Uri '{endpoint}' "
            "-ContentType 'application/json' "
            "-Headers @{ Authorization = $auth.AuthorizationHeader } "
            "-Body $body -ErrorAction Stop\n" + recipient_projection + "    $result.value\n}\n"
        )
    return "".join(pieces) + "Export-ModuleMember -Function " + ",".join(command_names) + "\n"
