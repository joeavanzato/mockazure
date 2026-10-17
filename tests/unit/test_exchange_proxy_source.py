import pytest


def test_proxy_has_only_declared_operations():
    from mockazure.providers.exchange.bootstrap import fixed_proxy_source

    endpoint = "https://outlook.mockazure.test/adminapi/v2.0/00000000-0000-0000-0000-000000000001/InvokeCommand"
    source = fixed_proxy_source(("Get-Mailbox", "Get-InboxRule", "Set-Mailbox"), endpoint)
    assert "function Get-Mailbox" in source
    assert "function Get-InboxRule" in source
    assert "GetAuthHeader" in source
    assert "ConvertTo-Json" in source
    assert "Invoke-RestMethod" in source
    assert "Invoke-Expression" not in source
    assert "function Get-EXO" not in source
    assert "$PSBoundParameters" in source
    for commands in (
        ("Invoke-Expression",),
        ("Get-Mailbox; Write-Host injected",),
        ("Get-EXOMailbox",),
    ):
        with pytest.raises(ValueError):
            fixed_proxy_source(commands, endpoint)
    for invalid in (
        "https://outlook.office365.com/evil",
        endpoint + "';exit",
        "http://localhost:8443/x",
    ):
        with pytest.raises(ValueError):
            fixed_proxy_source(("Get-Mailbox",), invalid)


def test_proxy_preserves_null_forwarding_values():
    from mockazure.providers.exchange.bootstrap import fixed_proxy_source

    endpoint = (
        "https://localhost:34443/adminapi/v2.0/00000000-0000-0000-0000-000000000001/InvokeCommand"
    )
    source = fixed_proxy_source(("Set-Mailbox",), endpoint)
    assert "[object]$ForwardingAddress" in source
    assert "[object]$ForwardingSmtpAddress" in source
