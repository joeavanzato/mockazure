"""Original Azure Tables client over CA-verified, exclusively local HTTPS."""

from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from urllib.parse import urlsplit

from azure.core import MatchConditions
from azure.core.credentials import AzureNamedKeyCredential, AzureSasCredential
from azure.core.pipeline.transport import RequestsTransport
from azure.data.tables import (
    EdmType,
    EntityProperty,
    TableClient,
    TableSasPermissions,
    TableServiceClient,
    TableTransactionError,
    UpdateMode,
    generate_table_sas,
)

from integrations.python.arm import LocalSession


def run_table(profile):
    endpoint = profile["table_endpoint"]
    parsed = urlsplit(endpoint)
    credential = AzureNamedKeyCredential(profile["account"], profile["key"])
    with LocalSession(parsed.scheme + "://" + parsed.netloc, profile["ca_file"]) as session:
        transport = RequestsTransport(session=session, connection_verify=profile["ca_file"])
        with TableServiceClient(
            endpoint,
            credential=credential,
            api_version="2019-02-02",
            transport=transport,
            retry_total=0,
        ) as service:
            table = service.create_table("PythonEvidence")
            assert "PythonEvidence" in [t.name for t in service.list_tables()]
            data = {
                "PartitionKey": "O'Brien",
                "RowKey": "one",
                "Count": EntityProperty(9007199254740993, EdmType.INT64),
                "Bytes": b"\x00\x01\xff",
                "When": datetime(2026, 10, 18, tzinfo=UTC),
            }
            table.create_entity(data)
            entity = table.get_entity("O'Brien", "one")
            assert entity["Count"].value == 9007199254740993 and entity["Bytes"] == b"\x00\x01\xff"
            table.update_entity(
                {"PartitionKey": "O'Brien", "RowKey": "one", "Keep": True},
                mode=UpdateMode.MERGE,
                etag=entity.metadata["etag"],
                match_condition=MatchConditions.IfNotModified,
            )
            table.upsert_entity(
                {
                    "PartitionKey": "O'Brien",
                    "RowKey": "two",
                    "Count": EntityProperty(9007199254740994, EdmType.INT64),
                },
                mode=UpdateMode.REPLACE,
            )
            table.submit_transaction(
                [
                    ("create", {"PartitionKey": "O'Brien", "RowKey": "three"}),
                    ("update", {"PartitionKey": "O'Brien", "RowKey": "two", "Batch": True}),
                ]
            )
            try:
                table.submit_transaction(
                    [
                        ("create", {"PartitionKey": "O'Brien", "RowKey": "rollback"}),
                        ("create", data),
                    ]
                )
            except TableTransactionError as error:
                assert error.status_code == 409 and error.index == 1
            else:
                raise AssertionError("Duplicate transaction unexpectedly succeeded")
            rows = list(table.query_entities("PartitionKey eq 'O''Brien'", results_per_page=1))
            assert {r["RowKey"] for r in rows} == {"one", "two", "three"}
            filtered = list(
                table.query_entities("Count ge 9007199254740993L", select=["RowKey", "Count"])
            )
            assert len(filtered) == 2 and filtered[0]["Count"].value == 9007199254740993
            sas = generate_table_sas(
                credential,
                "PythonEvidence",
                permission=TableSasPermissions(read=True),
                expiry=datetime.now(UTC) + timedelta(minutes=5),
                start_pk="O'Brien",
                end_pk="O'Brien",
                start_rk="two",
                end_rk="two",
            )
            with TableClient(
                endpoint,
                "PythonEvidence",
                credential=AzureSasCredential(sas),
                api_version="2019-02-02",
                transport=RequestsTransport(
                    session=session, session_owner=False, connection_verify=profile["ca_file"]
                ),
                retry_total=0,
            ) as signed:
                assert [r["RowKey"] for r in signed.list_entities()] == ["two"]
            table.delete_entity("O'Brien", "three")
            assert len(list(table.list_entities())) == 2
            return {
                "passed": True,
                "clientVersions": {"azure-data-tables": version("azure-data-tables")},
                "table": {
                    "precision": 9007199254740993,
                    "batchRollback": True,
                    "pagedEntities": 3,
                    "signedRange": True,
                    "remainingEntities": 2,
                },
            }
