"""Pinned Key Vault REST operations and strict writable shapes remain offline."""

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from mockazure.cloud.contracts import DATA, _catalog, verify_references
from mockazure.keyvault.contracts import checked_body, validate_shape
from mockazure.keyvault.endpoints import vault_origin, virtual_vault
from mockazure.providers.keyvault.errors import VaultError


def test_keyvault_source_operations_and_shapes():
    verify_references("keyvault")
    catalog = _catalog("keyvault")
    assert catalog == json.loads(
        Path("integrations/contracts/cloud/keyvault.json").read_text(encoding="utf-8")
    )
    for row in catalog["operations"]:
        schema = json.loads(
            (DATA / "keyvault" / row["sources"][0]["localPath"]).read_text(encoding="utf-8")
        )
        op = schema["paths"][row["path"]][row["method"].lower()]
        assert row["nativeOperationId"] == op["operationId"]
        assert all(int(code) in row["statusCodes"] for code in op["responses"] if code.isdigit())
    checked_body({"value": "secret", "attributes": {"enabled": True, "nbf": 1, "exp": 0}}, "set")
    validate_shape(
        "SecretBundle",
        {
            "id": "https://vault.localhost/secrets/name/version",
            "value": "secret",
            "attributes": {
                "enabled": True,
                "created": 0,
                "updated": 0,
                "recoverableDays": 90,
                "recoveryLevel": "Recoverable",
            },
        },
    )
    for body in (
        {"value": "x", "attributes": {"recoveryLevel": "Purgeable"}},
        {"value": "x", "attributes": {"enabled": 1}},
        {"value": "x", "tags": {"value": True}},
    ):
        with pytest.raises(VaultError):
            checked_body(body, "set")


@pytest.mark.parametrize(
    "host",
    [
        "vault.localhost.mockazure.test:bad",
        "[invalid",
        "vault.localhost.mockazure.test:8448/extra",
        "vault.localhost.mockazure.test:8448#fragment",
        "user@vault.localhost.mockazure.test:8448",
    ],
)
def test_keyvault_virtual_hosts_reject_malformed_authority(host):
    assert virtual_vault(host, "https://localhost:8448") is None


def test_keyvault_native_origin_is_local_and_has_no_routing_path():
    value = vault_origin("https://127.0.0.1:8448", "sample-vault")
    assert value == "https://sample-vault.127-0-0-1.mockazure.test:8448"
    assert urlsplit(value).path == ""
    assert virtual_vault(urlsplit(value).netloc, "https://127.0.0.1:8448") == "sample-vault"


@pytest.mark.parametrize("body", [{"value": "\ud800"}, {"value": "ok", "tags": {"\ud800": "x"}}])
def test_secret_rejects_unpaired_unicode(body):
    with pytest.raises(VaultError):
        checked_body(body, "set")
