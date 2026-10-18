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


def run_queue(profile):
    """Exercise default raw text and explicit SDK base64 policies without shims."""
    from azure.storage.queue import (
        AccountSasPermissions,
        QueueClient,
        QueueSasPermissions,
        QueueServiceClient,
        ResourceTypes,
        TextBase64DecodePolicy,
        TextBase64EncodePolicy,
        generate_account_sas,
        generate_queue_sas,
    )

    endpoint = profile["queue_endpoint"]
    parsed = urlsplit(endpoint)
    with LocalSession(parsed.scheme + "://" + parsed.netloc, profile["ca_file"]) as session:

        def transport():
            return RequestsTransport(
                session=session, session_owner=False, connection_verify=profile["ca_file"]
            )

        options = {"api_version": "2026-02-06", "retry_total": 0}
        with QueueServiceClient(
            endpoint, credential=profile["key"], transport=transport(), **options
        ) as service:
            queue = service.create_queue("python-messages", metadata={"source": "sdk"})
            assert "python-messages" in [
                q.name for q in service.list_queues(results_per_page=1, include_metadata=True)
            ]
            queue.set_queue_metadata({"source": "updated"})
            assert queue.get_queue_properties().metadata["source"] == "updated"
            text = "raw <payload> & unicode ☃"
            queue.send_message(text, time_to_live=-1)
            assert queue.peek_messages()[0].content == text
            received = next(queue.receive_messages(messages_per_page=1, visibility_timeout=30))
            assert received.content == text and received.dequeue_count == 1
            updated = queue.update_message(received, content="changed", visibility_timeout=0)
            assert updated.pop_receipt != received.pop_receipt
            received = next(queue.receive_messages(messages_per_page=1))
            assert received.content == "changed" and received.dequeue_count == 2
            queue.delete_message(received)
            assert queue.get_queue_properties().approximate_message_count == 0
            expiry = datetime.now(UTC) + timedelta(minutes=5)
            sas = generate_queue_sas(
                profile["account"],
                "python-messages",
                account_key=profile["key"],
                permission=QueueSasPermissions(read=True, add=True, update=True, process=True),
                expiry=expiry,
            )
            with QueueClient(
                endpoint, "python-messages", credential=sas, transport=transport(), **options
            ) as signed:
                sent = signed.send_message("signed")
                assert signed.peek_messages()[0].content == "signed"
                signed.delete_message(sent)
            account_sas = generate_account_sas(
                profile["account"],
                profile["key"],
                ResourceTypes(service=True, container=True, object=True),
                AccountSasPermissions(
                    read=True,
                    write=True,
                    delete=True,
                    list=True,
                    add=True,
                    update=True,
                    process=True,
                ),
                expiry,
            )
            with QueueClient(
                endpoint,
                "python-messages",
                credential=account_sas,
                transport=transport(),
                **options,
            ) as signed:
                sent = signed.send_message("account-sas")
                updated = signed.update_message(
                    sent, content="updated-account-sas", visibility_timeout=0
                )
                signed.delete_message(updated)
            with QueueClient(
                endpoint,
                "python-messages",
                credential=profile["key"],
                transport=transport(),
                message_encode_policy=TextBase64EncodePolicy(),
                message_decode_policy=TextBase64DecodePolicy(),
                **options,
            ) as encoded:
                encoded.send_message(text)
                import base64

                assert queue.peek_messages()[0].content == base64.b64encode(text.encode()).decode()
                assert encoded.peek_messages()[0].content == text
                encoded.clear_messages()
            queue.delete_queue()
    return {
        "passed": True,
        "queue": {
            "sdk": version("azure-storage-queue"),
            "rawAndBase64": True,
            "sas": True,
            "receipts": True,
        },
    }
