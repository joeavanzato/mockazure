"""Public actions against the same business data that collectors read."""

from tests.integration.graph_support import graph_client
from tests.integration.test_connected_collaboration import populate
from tests.integration.test_graph_send_workflows import deliver
from tests.integration.workflow_support import headers_for
from tests.unit.test_business_relationships import values


async def test_connected_file_upload_versions_move_copy_delete(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        _, data = await populate(database, settings, tenant)
        headers = await headers_for(database, settings, tenant, app, None, ["Files.ReadWrite.All"])
        drive, item = data["drives"].items[0].id, data["documents"].items[0].id
        folder, root = data["folders"].items[0].id, data["roots"].items[0].id
        base = f"/v1.0/drives/{drive}/items/"
        old = await client.get(base + item + "/content", headers=headers, follow_redirects=True)
        assert old.status_code == 200
        changed = await client.put(
            base + item + "/content", headers=headers, content=b"Updated business document"
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["id"] == item
        assert (
            await client.get(base + item + "/content", headers=headers, follow_redirects=True)
        ).content == b"Updated business document"
        versions = (await client.get(base + item + "/versions", headers=headers)).json()["value"]
        contents = [
            (
                await client.get(
                    base + item + "/versions/" + v["id"] + "/content",
                    headers=headers,
                    follow_redirects=True,
                )
            ).content
            for v in versions
        ]
        assert old.content in contents
        moved = await client.patch(
            base + item,
            headers=headers,
            json={"name": "Moved.txt", "parentReference": {"id": root}},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["parentReference"]["id"] == root
        copied = await client.post(
            base + item + "/copy",
            headers=headers,
            json={"name": "Copy.txt", "parentReference": {"id": folder}},
        )
        assert copied.status_code == 202, copied.text
        monitor = await client.get(copied.headers["Location"], headers=headers)
        assert monitor.status_code == 303, monitor.text
        copy = await client.get(monitor.headers["Location"], headers=headers)
        assert copy.status_code == 200 and copy.json()["id"] != item
        assert (
            await client.get(
                monitor.headers["Location"] + "/content", headers=headers, follow_redirects=True
            )
        ).content == b"Updated business document"
        assert (await client.delete(base + item, headers=headers)).status_code == 204
        assert (await client.get(base + item, headers=headers)).status_code == 404
        assert (await client.get(monitor.headers["Location"], headers=headers)).status_code == 200


async def test_file_writes_require_current_write_authority_and_matching_etag(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, outsider):
        _, data = await populate(database, settings, tenant)
        drive, item = data["drives"].items[0].id, data["documents"].items[0].id
        path = f"/v1.0/drives/{drive}/items/{item}"
        read = await headers_for(database, settings, tenant, app, None, ["Files.Read.All"])
        outsider_write = await headers_for(
            database, settings, tenant, app, outsider, ["Files.ReadWrite"], kind="delegated"
        )
        for denied in (read, outsider_write):
            result = await client.put(path + "/content", headers=denied, content=b"denied")
            assert result.status_code == 403, result.text
        write = await headers_for(database, settings, tenant, app, None, ["Files.ReadWrite.All"])
        stale = await client.patch(
            path, headers=write | {"If-Match": '"stale"'}, json={"name": "stale.txt"}
        )
        assert stale.status_code == 412, stale.text
        foreign = data["folders"].items[1].id
        moved = await client.patch(path, headers=write, json={"parentReference": {"id": foreign}})
        assert moved.status_code == 404, moved.text


async def test_connected_mail_reply_forward_and_calendar_actions(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        _, data = await populate(database, settings, tenant)
        headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            ["Mail.ReadWrite", "Mail.Send", "Calendars.ReadWrite"],
        )
        source = values("messages", 4, tenant=str(tenant))[1]
        message = f"/v1.0/users/{source.parent}/messages/{source.payload['message']['id']}"
        reply = await client.post(
            message + "/reply", headers=headers, json={"comment": "Review complete"}
        )
        assert reply.status_code == 202, reply.text
        await deliver(database, tenant)
        target = values("messages", 4, tenant=str(tenant))[0].payload["message"]["payload"]["from"]
        forward = await client.post(
            message + "/forward", headers=headers, json={"comment": "FYI", "toRecipients": [target]}
        )
        assert forward.status_code == 202, forward.text
        await deliver(database, tenant)
        messages = await client.get(f"/v1.0/users/{source.parent}/messages", headers=headers)
        assert messages.status_code == 200 and len(messages.json()["value"]) > 2
        event = data["events"].items[0]
        changed = await client.patch(
            f"/v1.0/users/{event.parent}/events/{event.id}",
            headers=headers,
            json={"subject": "Updated project review"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["subject"] == "Updated project review"


async def test_connected_site_permission_grants_selected_app_file_write(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        _, data = await populate(database, settings, tenant)
        site = data["sites"].items[0].id
        drive, item = data["drives"].items[0].id, data["documents"].items[0].id
        path = f"/v1.0/drives/{drive}/items/{item}"
        selected = await headers_for(database, settings, tenant, app, None, ["Sites.Selected"])
        denied = await client.put(path + "/content", headers=selected, content=b"no grant")
        assert denied.status_code == 403
        admin = await headers_for(database, settings, tenant, app, None, ["Sites.FullControl.All"])
        grant = await client.post(
            f"/v1.0/sites/{site}/permissions",
            headers=admin,
            json={
                "roles": ["write"],
                "grantedToIdentities": [{"application": {"id": str(app)}}],
            },
        )
        assert grant.status_code == 201, grant.text
        written = await client.put(path + "/content", headers=selected, content=b"selected app")
        assert written.status_code == 200, written.text
        removed = await client.delete(
            f"/v1.0/sites/{site}/permissions/{grant.json()['id']}", headers=admin
        )
        assert removed.status_code == 204, removed.text
        assert (await client.get(path, headers=selected)).status_code == 403
