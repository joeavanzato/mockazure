"""Vault and secret metadata only; neither ciphertext nor values are projected."""

from mockazure.keyvault.models import Vault
from mockazure.keyvault.secret_models import Secret, SecretVersion
from mockazure.population.adapters.browsing import browser


def adapters():
    v, s, r = Vault, Secret, SecretVersion
    return [
        browser(
            "keyvault.vaults",
            "Key Vault",
            "/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.KeyVault/"
            "vaults/{vault}",
            v,
            v.scope_key,
            v.name,
            {"id": v.scope_key, "name": v.name},
        ),
        browser(
            "keyvault.secrets",
            "Key Vault",
            "/{vault}/secrets",
            s,
            s.id,
            s.revision,
            {"id": s.id, "name": s.name, "deletedAt": s.deleted_at, "versions": s.revision},
            parent=s.vault_id,
        ),
        browser(
            "keyvault.versions",
            "Key Vault",
            "/{vault}/secrets/{secret}/versions",
            r,
            r.id,
            r.updated_at,
            {
                "id": r.id,
                "version": r.version,
                "attributes": r.attributes,
                "tags": r.tags,
                "contentType": r.content_type,
            },
            parent=r.secret_id,
        ),
    ]
