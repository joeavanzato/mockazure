"""An explicit local receiver registry is the network authority, never callback DNS."""

import pytest

from mockazure.providers.graph.errors import GraphError


def test_receiver_uses_registered_literal_address():
    from mockazure.notifications.receivers import resolve_receiver

    url = "https://receiver.test:443/callback?channel=local"
    binding = resolve_receiver(url, {url: {"address": "127.0.0.1", "ca_file": "local-ca.pem"}})
    assert binding["address"] == "127.0.0.1" and binding["hostname"] == "receiver.test"


@pytest.mark.parametrize(
    "url,address",
    [
        ("http://localhost/cb", "127.0.0.1"),
        ("https://localhost/cb", "8.8.8.8"),
        ("https://localhost/cb", "169.254.169.254"),
        ("https://localhost/cb", "example.com"),
        ("https://user:password@localhost/cb", "127.0.0.1"),
        ("https://localhost/cb#fragment", "127.0.0.1"),
    ],
)
def test_receiver_rejects_nonlocal_or_ambiguous_bindings(url, address):
    from mockazure.notifications.receivers import resolve_receiver

    with pytest.raises(GraphError):
        resolve_receiver(url, {url: {"address": address, "ca_file": "local-ca.pem"}})


def test_notification_envelope_matches_operation_semantics():
    from mockazure.notifications.contracts import validate_notification

    base = {
        "subscriptionId": "00000000-0000-0000-0000-000000000001",
        "tenantId": "00000000-0000-0000-0000-000000000002",
        "subscriptionExpirationDateTime": "2026-10-25T00:00:00Z",
    }
    validate_notification({**base, "lifecycleEvent": "missed"})
    validate_notification(
        {
            **base,
            "changeType": "created",
            "resource": "users/u/messages/m",
            "resourceData": {
                "@odata.type": "#microsoft.graph.message",
                "@odata.id": "users/u/messages/m",
                "id": "m",
            },
        }
    )
    for extra in [
        {"changeType": "created", "lifecycleEvent": "missed"},
        {"lifecycleEvent": "invented"},
        {
            "changeType": "created",
            "resource": "users/u/messages/m",
            "resourceData": {"id": "m", "subject": "leaked"},
        },
    ]:
        with pytest.raises(ValueError):
            validate_notification({**base, **extra})


def test_subscription_operation_catalog_is_offline_and_schema_bound():
    from mockazure.cloud.contracts import load_contract, verify_references

    verify_references("graph-workflows")
    for operation, method in [
        ("create", "POST"),
        ("list", "GET"),
        ("get", "GET"),
        ("update", "PATCH"),
        ("delete", "DELETE"),
        ("reauthorize", "POST"),
    ]:
        contract = load_contract("graph-workflows", "subscription." + operation)
        assert contract["version"] == "v1.0" and contract["method"] == method
        assert contract["sources"] and not contract["queryOptions"]
