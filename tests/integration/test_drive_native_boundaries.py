"""File action boundaries that ordinary imported-history tests cannot exercise."""

from sqlalchemy import select

from mockazure.workloads.models import WorkloadRelation as E
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_connected_collaboration import populate


async def test_upload_new_replace_and_remove_incident_edges(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        _, data = await populate(database, settings, tenant)
        headers = await headers_for(database, settings, tenant, app, None, ["Files.ReadWrite.All"])
        drive, parent = data["drives"].items[0].id, data["folders"].items[0].id
        root = f"/v1.0/drives/{drive}/items/"
        created = await client.put(
            root + parent + ":/new.txt:/content", headers=headers, content=b"first"
        )
        assert created.status_code == 201, created.text
        item = created.json()["id"]
        second = await client.put(
            root + item + "/content",
            headers=headers | {"If-Match": created.json()["eTag"]},
            content=b"second",
        )
        assert second.status_code == 200
        assert (
            await client.put(
                root + parent + ":/new.txt:/content", headers=headers, content=b"third"
            )
        ).json()["id"] == item
        assert (await client.delete(root + item, headers=headers)).status_code == 204
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(E.source_id)
                    .where(
                        E.tenant_id == tenant,
                        E.workload == "sharepoint",
                        E.source_id == item,
                        E.valid_to.is_(None),
                    )
                    .limit(1)
                )
                is None
            )


async def test_copy_monitor_is_bound_to_caller_and_file_content_is_tenant_local(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, data = await populate(database, settings, tenant)
        headers = await headers_for(database, settings, tenant, app, None, ["Files.ReadWrite.All"])
        drive, item = data["drives"].items[0].id, data["documents"].items[0].id
        root = f"/v1.0/drives/{drive}/items/{item}"
        copied = await client.post(
            root + "/copy", headers=headers, json={"name": "Private-copy.pdf"}
        )
        assert copied.status_code == 202, copied.text
        other = await headers_for(
            database, settings, tenant, app, user, ["Files.ReadWrite.All"], kind="delegated"
        )
        assert (await client.get(copied.headers["Location"], headers=other)).status_code == 403
        invalid_media = await client.put(
            root + "/content", headers=headers | {"Content-Type": "not a mime"}, content=b"x"
        )
        assert invalid_media.status_code == 400, invalid_media.text
        invalid_name = await client.patch(root, headers=headers, json={"name": "../escape"})
        assert invalid_name.status_code == 400
