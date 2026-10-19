"""Unmodified Azure SecretClient over exclusively local, CA-verified HTTPS."""

from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from urllib.parse import urlsplit

from azure.core.pipeline.transport import RequestsTransport
from azure.keyvault.secrets import SecretClient

from integrations.python.arm import Credential, LocalSession


def run_keyvault(profile):
    endpoint = profile["vault_endpoint"]
    parsed = urlsplit(endpoint)

    class TrackedCredential(Credential):
        def __init__(self, raw):
            super().__init__(raw)
            self.scopes = []

        def get_token(self, *scopes, **kwargs):
            assert scopes == ("https://vault.azure.net/.default",)
            self.scopes.append(scopes)
            return super().get_token(*scopes, **kwargs)

    credential = TrackedCredential(profile["token"])
    with LocalSession(parsed.scheme + "://" + parsed.netloc, profile["ca_file"]) as session:
        transport = RequestsTransport(
            session=session, session_owner=False, connection_verify=profile["ca_file"]
        )
        with SecretClient(
            endpoint,
            credential=credential,
            api_version="2025-07-01",
            transport=transport,
            retry_total=0,
            verify_challenge_resource=False,
        ) as client:
            a = client.set_secret(
                "python-secret", "one ☃", content_type="text/plain", tags={"source": "sdk"}
            )
            b = client.set_secret("python-secret", "two")
            assert a.properties.version != b.properties.version
            assert a.name == b.name == "python-secret"
            assert client.get_secret("python-secret", a.properties.version).value == "one ☃"
            assert client.get_secret("python-secret").value == "two"
            assert [s.name for s in client.list_properties_of_secrets()] == ["python-secret"]
            versions = list(
                client.list_properties_of_secret_versions("python-secret", max_page_size=1)
            )
            assert len(versions) == 2
            changed = client.update_secret_properties(
                "python-secret",
                b.properties.version,
                enabled=True,
                not_before=datetime.now(UTC) + timedelta(days=1),
                expires_on=datetime.now(UTC) - timedelta(days=1),
            )
            assert changed.enabled
            assert client.get_secret("python-secret").value == "two"
            if profile.get("recovery"):
                backup = client.backup_secret("python-secret")
                assert isinstance(backup, bytes) and b"one" not in backup
                deleted = client.begin_delete_secret("python-secret").result()
                assert deleted.name == "python-secret" and deleted.deleted_date
                assert client.get_deleted_secret("python-secret").name == "python-secret"
                assert [s.name for s in client.list_deleted_secrets()] == ["python-secret"]
                client.begin_recover_deleted_secret("python-secret").result()
                assert client.get_secret("python-secret", a.properties.version).value == "one ☃"
                client.begin_delete_secret("python-secret").result()
                client.purge_deleted_secret("python-secret")
                restored = client.restore_secret_backup(backup)
                assert restored.version == b.properties.version
                assert client.get_secret("python-secret").value == "two"
    assert credential.scopes
    return {
        "passed": True,
        "sdk": version("azure-keyvault-secrets"),
        "versions": 2,
        "recovery": bool(profile.get("recovery")),
        "challengeScopeVerified": True,
        "temporalMetadataGet": True,
    }
