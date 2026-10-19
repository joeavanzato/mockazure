"""Storage metadata and non-consuming queue inspection; no keys or receipt digests."""

from mockazure.population.adapters.browsing import browser
from mockazure.storage.blob_models import Blob
from mockazure.storage.file_models import FileNode
from mockazure.storage.models import StorageAccount, StorageNamespace
from mockazure.storage.queue_models import QueueMessage
from mockazure.storage.table_models import Table, TableEntity


def adapters():
    a, n, b, f, q, t, e = (
        StorageAccount,
        StorageNamespace,
        Blob,
        FileNode,
        QueueMessage,
        Table,
        TableEntity,
    )
    result = [
        browser(
            "storage.accounts",
            "Azure Storage",
            "/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.Storage/s"
            "torageAccounts/{account}",
            a,
            a.scope_key,
            a.key1_created,
            {"id": a.scope_key, "name": a.name},
        )
    ]
    for key, service, path in (
        ("containers", "blob", "/{account}/{container}?restype=container"),
        ("shares", "file", "/{account}/{share}?restype=share"),
        ("queues", "queue", "/{account}/{queue}"),
    ):
        result.append(
            browser(
                "storage." + key,
                "Azure Storage",
                path,
                n,
                n.name,
                n.etag,
                {"name": n.name, "service": n.service, "createdAt": n.created_at},
                parent=n.account_id,
                conditions=(n.service == service,),
            )
        )
    result.extend(
        [
            browser(
                "storage.blobs",
                "Azure Storage",
                "/{account}/{container}/{blob}",
                b,
                b.id,
                b.changed_at,
                {
                    "id": b.id,
                    "name": b.name,
                    "container": b.container,
                    "currentVersionId": b.current_id,
                },
                parent=b.account_id,
                conditions=(b.current_id.is_not(None),),
            ),
            browser(
                "storage.files",
                "Azure Storage",
                "/{account}/{share}/{path}",
                f,
                f.id,
                f.etag,
                {
                    "id": f.id,
                    "path": f.path,
                    "directory": f.directory,
                    "size": f.size,
                    "contentId": f.content_id,
                },
                parent=f.tree_id,
            ),
            browser(
                "storage.tables",
                "Azure Storage",
                "/{account}/Tables",
                t,
                t.id,
                t.created_at,
                {"id": t.id, "name": t.name},
                parent=t.account_id,
            ),
            browser(
                "storage.entities",
                "Azure Storage",
                "/{account}/{table}(PartitionKey='{partition}',RowKey='{row}')",
                e,
                e.id,
                e.etag,
                {
                    "id": e.id,
                    "PartitionKey": e.partition_key,
                    "RowKey": e.row_key,
                    "properties": e.properties,
                },
                parent=e.table_id,
            ),
            browser(
                "storage.messages",
                "Azure Storage",
                "/{account}/{queue}/messages?peekonly=true",
                q,
                q.id,
                q.visible_at,
                {
                    "id": q.id,
                    "queue": q.queue,
                    "body": q.body,
                    "dequeueCount": q.dequeue_count,
                    "expiresAt": q.expires_at,
                    "visibleAt": q.visible_at,
                },
                parent=q.account_id,
                limitations=[
                    "Administrative non-consuming view includes delayed/expired rows until "
                    "cleanup; public peek applies visibility."
                ],
            ),
        ]
    )
    return result
