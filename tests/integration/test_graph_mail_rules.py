import asyncio

from mockazure.providers.graph.schema_reference import validate_payload
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_mailbox_state import provision


def body(name="Rule", sequence=1):
    return {
        "displayName": name,
        "sequence": sequence,
        "isEnabled": True,
        "conditions": {"subjectContains": ["invoice"]},
        "actions": {
            "forwardTo": [
                {"emailAddress": {"address": "investigator@example.test", "name": "Investigator"}}
            ],
            "stopProcessingRules": True,
        },
    }


async def test_rule_crud_actual_sdk_shapes(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(
            client,
            tenant,
            user,
            folders=[
                {"id": "inbox", "name": "Inbox", "path": "/Inbox"},
                {"id": "review", "name": "Review", "path": "/Review"},
            ],
        )
        headers = await headers_for(
            database, settings, tenant, app, None, {"MailboxSettings.ReadWrite"}
        )
        path = f"/v1.0/users/{user}/mailFolders/inbox/messageRules"
        created = await client.post(path, headers=headers, json=body())
        assert created.status_code == 201, created.text
        rule = created.json()
        validate_payload("v1.0", "messageRule", rule)
        identifier = rule["id"]
        changed = await client.patch(
            path + "/" + identifier,
            headers=headers,
            json={
                "actions": {"moveToFolder": "review"},
                "isEnabled": False,
            },
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["id"] == identifier
        assert changed.json()["conditions"] == body()["conditions"]
        assert changed.json()["actions"] == {"moveToFolder": "review"}
        read = await client.get(path + "/" + identifier, headers=headers)
        assert read.json() == changed.json()
        assert (await client.delete(path + "/" + identifier, headers=headers)).status_code == 204
        assert (await client.get(path + "/" + identifier, headers=headers)).status_code == 404
        assert (await client.get(path, headers=headers)).json()["value"] == []
        for bad in (
            {"actions": {"delete": True, "moveToFolder": "review"}},
            {"actions": {"moveToFolder": "absent"}},
            {"isReadOnly": False},
            {"actions": {"forwardTo": [{"emailAddress": {"address": "broken"}}]}},
        ):
            response = await client.post(path, headers=headers, json=body() | bad)
            assert response.status_code == 400, response.text
        for option in ("$filter=isEnabled eq true", "$expand=actions", "$orderby=displayName"):
            assert (await client.get(path + "?" + option, headers=headers)).status_code == 400


async def test_mailbox_self_and_application_permissions(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(client, tenant, user)
        path = f"/v1.0/users/{user}/mailFolders/inbox/messageRules"
        me = "/v1.0/me/mailFolders/inbox/messageRules"
        app_headers = await headers_for(
            database, settings, tenant, app, None, {"MailboxSettings.ReadWrite"}
        )
        self_headers = await headers_for(
            database, settings, tenant, app, user, {"MailboxSettings.ReadWrite"}, kind="delegated"
        )
        read_headers = await headers_for(
            database, settings, tenant, app, user, {"MailboxSettings.Read"}, kind="delegated"
        )
        assert (await client.post(me, headers=self_headers, json=body())).status_code == 201
        assert (await client.get(me, headers=read_headers)).status_code == 200
        assert (await client.post(me, headers=read_headers, json=body())).status_code == 403
        assert (await client.get(me, headers=app_headers)).status_code == 400
        other = (
            await client.post(
                f"/_mock/tenants/{tenant}/identity/users",
                json={
                    "upn": "other@contoso.test",
                    "display_name": "Other",
                    "password": "local-other-password",
                },
            )
        ).json()["id"]
        assert (
            await client.put(
                f"/_mock/tenants/{tenant}/mail/mailboxes/{other}",
                json={
                    "primary_smtp": "other@contoso.test",
                    "alias": "other",
                },
            )
        ).status_code == 200
        other_path = f"/v1.0/users/{other}/mailFolders/inbox/messageRules"
        assert (await client.get(other_path, headers=self_headers)).status_code == 403
        assert (await client.get(other_path, headers=app_headers)).status_code == 200
        admin_path = f"/_mock/tenants/{tenant}/mail/mailboxes/{user}/rules"
        readonly = await client.post(
            admin_path,
            json={
                "rule": body("Readonly"),
                "read_only": True,
            },
        )
        assert readonly.status_code == 201, readonly.text
        hidden = await client.post(admin_path, json={"rule": body("Hidden"), "hidden": True})
        assert hidden.status_code == 201, hidden.text
        rows = (await client.get(path, headers=app_headers)).json()["value"]
        assert "Hidden" not in {r["displayName"] for r in rows}
        for method in ("patch", "delete"):
            response = await client.request(
                method,
                path + "/" + readonly.json()["id"],
                headers=app_headers,
                json={"isEnabled": False} if method == "patch" else None,
            )
            assert response.status_code == 403
        assert (
            await client.get(path + "/" + hidden.json()["id"], headers=app_headers)
        ).status_code == 404
        created = await asyncio.gather(
            *(
                client.post(path, headers=app_headers, json=body(f"Concurrent {i}", 1))
                for i in range(4)
            )
        )
        assert all(r.status_code == 201 for r in created)
        moved = await asyncio.gather(
            *(
                client.patch(path + "/" + r.json()["id"], headers=app_headers, json={"sequence": 1})
                for r in created
            )
        )
        assert all(r.status_code == 200 for r in moved)
        rows = (await client.get(path, headers=app_headers)).json()["value"]
        assert len({r["sequence"] for r in rows}) == len(rows)


async def test_rule_page_snapshot(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(client, tenant, user)
        headers = await headers_for(
            database, settings, tenant, app, None, {"MailboxSettings.ReadWrite"}
        )
        path = f"/v1.0/users/{user}/mailFolders/inbox/messageRules"
        identifiers = []
        for i in range(4):
            response = await client.post(path, headers=headers, json=body(f"Rule {i}", i + 1))
            assert response.status_code == 201, response.text
            identifiers.append(response.json()["id"])
        first = await client.get(path + "?$top=2&$select=id,displayName,sequence", headers=headers)
        assert first.status_code == 200, first.text
        link = first.json()["@odata.nextLink"]
        assert [v["id"] for v in first.json()["value"]] == identifiers[:2]
        assert (
            await client.delete(path + "/" + identifiers[2], headers=headers)
        ).status_code == 204
        assert (
            await client.patch(
                path + "/" + identifiers[3],
                headers=headers,
                json={"displayName": "Renamed", "sequence": 1},
            )
        ).status_code == 200
        page = await client.get(link, headers=headers)
        assert page.status_code == 200, page.text
        assert [v["id"] for v in page.json()["value"]] == identifiers[2:]
        assert [v["displayName"] for v in page.json()["value"]] == ["Rule 2", "Rule 3"]
        assert page.json() == (await client.get(link, headers=headers)).json()
        assert (await client.get(link + "&$top=5", headers=headers)).status_code == 400
        changed_permission = await headers_for(
            database, settings, tenant, app, None, {"MailboxSettings.Read"}
        )
        assert (await client.get(link, headers=changed_permission)).status_code == 400
        assert (
            await client.put(
                f"/_mock/tenants/{tenant}/mail/settings",
                json={
                    "exchange_enabled": False,
                },
            )
        ).status_code == 200
        assert (await client.get(link, headers=headers)).status_code == 403
