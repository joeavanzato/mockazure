import base64

from tests.integration.graph_support import graph_client
from tests.integration.test_exchange_mail import exchange_headers
from tests.integration.test_mailbox_state import provision


async def test_compiled_exo_reads(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(
            client,
            tenant,
            user,
            archive_enabled=True,
            folders=[
                {
                    "id": "inbox",
                    "name": "Inbox",
                    "path": "/Inbox",
                    "item_count": 12,
                    "size_bytes": 4096,
                },
                {
                    "id": "archive",
                    "name": "Archive",
                    "path": "/",
                    "archive": True,
                    "item_count": 3,
                    "size_bytes": 2048,
                },
            ],
        )
        roles = f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/"
        assert (await client.put(roles + "Global Reader")).status_code == 200
        headers = await exchange_headers(database, settings, tenant, app)
        key = base64.b64encode(str(user).encode()).decode()
        path = f"/adminapi/beta/{tenant}/Mailbox('{key}')"
        response = await client.get(path, params={"isEncoded": "true"}, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ExternalDirectoryObjectId"] == str(user)
        assert body["ArchiveDatabase"] == "MockAzure-Archive-" + body["ArchiveGuid"]
        assert body["ObjectKey"] == key
        assert "EffectiveForwardingAddress" not in body
        selected = await client.get(
            path, params={"$select": "DisplayName,ExchangeGuid"}, headers=headers
        )
        assert selected.status_code == 200, selected.text
        assert set(selected.json()) == {
            "@odata.context",
            "ObjectKey",
            "DisplayName",
            "ExchangeGuid",
        }
        assert (
            await client.get(path, params={"$filter": "nonsense"}, headers=headers)
        ).status_code == 400
        stats = await client.get(
            path + "/Exchange.GetMailboxStatistics()", params={"isEncoded": "true"}, headers=headers
        )
        assert stats.status_code == 200, stats.text
        assert stats.json()["value"][0]["TotalItemSize"] == 4096
        assert stats.json()["value"][0]["ItemCount"] == 12
        archive = await client.get(
            path + "/Exchange.GetMailboxStatistics()",
            params={"isEncoded": "true", "Archive": "True"},
            headers=headers,
        )
        assert archive.status_code == 200, archive.text
        assert archive.json()["value"][0]["ItemCount"] == 3
        assert archive.json()["value"][0]["TotalItemSize"] == 2048
        assert archive.json()["value"][0]["IsArchiveMailbox"] is True

        folder_path = (
            path + "/MailboxFolder/Exchange.GetMailboxFolderStatistics"
            "(folderscope=Exchange.ElcFolderType'All')"
        )
        folders = await client.get(folder_path, params={"isEncoded": "true"}, headers=headers)
        assert folders.status_code == 200, folders.text
        assert [
            (row["Name"], row["ItemsInFolder"], row["FolderSize"])
            for row in folders.json()["value"]
        ] == [("Inbox", 12, 4096)]
        cas = await client.get(
            path.replace("/Mailbox(", "/CasMailbox("), params={"isEncoded": "true"}, headers=headers
        )
        assert cas.status_code == 200, cas.text
        assert cas.json()["ImapEnabled"] is True
        assert cas.json()["ExternalDirectoryObjectId"] == str(user)
        assert (await client.delete(roles + "Global Reader")).status_code == 200
        assert (await client.get(path, headers=headers)).status_code == 403
