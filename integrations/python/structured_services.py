"""Original Python clients retain evidence across restart and metadata/value restore."""

import argparse
import json
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from azure.core.credentials import AzureNamedKeyCredential
from azure.core.pipeline.transport import RequestsTransport
from azure.data.tables import EdmType, EntityProperty, TableClient
from azure.keyvault.secrets import SecretClient
from azure.storage.queue import QueueClient

from integrations.python.arm import Credential, LocalSession
from integrations.python.keyvault_secrets import run_keyvault
from integrations.python.storage_structured import run_queue, run_table


def run(profile, mode, backup_file):
    assertions = {}
    if mode == "write":
        assert run_table(profile)["table"]["batchRollback"]
        assert run_queue(profile)["queue"]["receipts"]
        assert run_keyvault(profile | {"recovery": True})["recovery"]
        assertions.update(tableMutation=True, queueReceipts=True, secretRecovery=True)
    for service in ("table", "queue", "vault"):
        endpoint = profile[service + "_endpoint"]
        parsed = urlsplit(endpoint)
        with LocalSession(parsed.scheme + "://" + parsed.netloc, profile["ca_file"]) as session:
            transport = RequestsTransport(
                session=session, session_owner=False, connection_verify=profile["ca_file"]
            )
            if service == "table":
                with TableClient(
                    endpoint,
                    "PythonPersist",
                    credential=AzureNamedKeyCredential(profile["account"], profile["key"]),
                    transport=transport,
                    api_version="2019-02-02",
                    retry_total=0,
                ) as client:
                    if mode == "write":
                        client.create_table()
                        client.create_entity(
                            {
                                "PartitionKey": "p",
                                "RowKey": "one",
                                "Count": EntityProperty(9007199254740993, EdmType.INT64),
                                "Keep": True,
                                "Bytes": b"\x00\x01\xff",
                            }
                        )
                    entity = client.get_entity("p", "one")
                    assert (
                        entity["Count"].value == 9007199254740993
                        and entity["Keep"]
                        and entity["Bytes"] == b"\x00\x01\xff"
                    )
            elif service == "queue":
                with QueueClient(
                    endpoint,
                    "python-persist",
                    credential=profile["key"],
                    transport=transport,
                    api_version="2026-02-06",
                    retry_total=0,
                ) as client:
                    if mode == "write":
                        client.create_queue()
                        client.send_message("persistent ☃", time_to_live=-1)
                    assert client.peek_messages()[0].content == "persistent ☃"
            else:
                with SecretClient(
                    endpoint,
                    Credential(profile["token"]),
                    transport=transport,
                    api_version="2025-07-01",
                    retry_total=0,
                    verify_challenge_resource=False,
                ) as client:
                    if mode == "write":
                        backup_file.write_bytes(client.backup_secret("python-secret"))
                    if mode in {"write", "restore"}:
                        client.begin_delete_secret("python-secret").result()
                        client.purge_deleted_secret("python-secret")
                        client.restore_secret_backup(backup_file.read_bytes())
                        assertions["secretBackupRestore"] = True
                    assert client.get_secret("python-secret").value == "two"
                    assert (
                        len(list(client.list_properties_of_secret_versions("python-secret"))) == 2
                    )
    return {
        "passed": True,
        "mode": mode,
        "assertions": assertions | {"persistentRead": True},
        "clientVersions": {
            name: version(name)
            for name in ("azure-data-tables", "azure-storage-queue", "azure-keyvault-secrets")
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--mode", choices=["write", "read", "restore"], required=True)
    args = parser.parse_args()
    result = run(json.loads(args.profile.read_text(encoding="utf-8")), args.mode, args.backup)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
