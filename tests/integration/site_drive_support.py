from uuid import uuid4

from tests.integration.test_content_storage import upload


async def seed_site(client, database, tenant, user, *, name="Evidence", suffix="one"):
    site = f"contoso.test,{uuid4()},{uuid4()}"
    drive, root, item = f"drive!{suffix}", f"root!{suffix}", f"item!{suffix}%tag"
    blob = await upload(database, tenant, b"local drive evidence\x00bytes")

    def record(kind, identifier, payload, content=None):
        if kind in {"site", "drive", "driveItem", "list", "listItem"}:
            payload = {
                "createdDateTime": "2026-10-07T00:00:00Z",
                "lastModifiedDateTime": "2026-10-07T00:00:00Z",
                **payload,
            }
        return {
            "kind": kind,
            "id": identifier,
            "payload": {"id": identifier, **payload},
            **({"content_id": str(content)} if content else {}),
        }

    def edge(source_kind, source, relation, target_kind, target):
        return {
            "source": {"kind": source_kind, "id": source},
            "relation": relation,
            "target": {"kind": target_kind, "id": target},
        }

    records = [
        record(
            "site",
            site,
            {"displayName": name, "name": suffix, "webUrl": f"https://contoso.test/sites/{suffix}"},
        ),
        record("drive", drive, {"name": "Documents", "driveType": "documentLibrary"}),
        record("driveItem", root, {"name": "root", "root": {}, "folder": {"childCount": 1}}),
        record(
            "driveItem",
            item,
            {"name": "evidence #1.txt", "file": {"mimeType": "text/plain"}, "size": blob.size},
            blob.id,
        ),
        record(
            "list",
            "list!" + suffix,
            {"displayName": "Findings", "list": {"template": "genericList"}},
        ),
        record(
            "listItem",
            "li!" + suffix,
            {"webUrl": f"https://contoso.test/sites/{suffix}/Lists/Findings/1"},
        ),
        record("fieldValueSet", "fields!" + suffix, {"Title": "Finding", "Score": 42}),
        record(
            "permission",
            "evidence-permission!" + suffix,
            {
                "roles": ["read"],
                "grantedToV2": {"user": {"id": str(user), "displayName": "Observed reader"}},
            },
        ),
        record("driveItemVersion", "version!" + suffix, {"size": blob.size}, blob.id),
    ]
    edges = [
        edge("site", site, "drives", "drive", drive),
        edge("drive", drive, "root", "driveItem", root),
        edge("driveItem", root, "children", "driveItem", item),
        edge("site", site, "lists", "list", "list!" + suffix),
        edge("list", "list!" + suffix, "items", "listItem", "li!" + suffix),
        edge("listItem", "li!" + suffix, "fields", "fieldValueSet", "fields!" + suffix),
        edge("driveItem", item, "permissions", "permission", "evidence-permission!" + suffix),
        edge("driveItem", item, "versions", "driveItemVersion", "version!" + suffix),
    ]
    batch = {"scope": site, "expected_revision": 0, "records": records, "edges": edges}
    response = await client.post(f"/_mock/tenants/{tenant}/workloads/sharepoint/import", json=batch)
    assert response.status_code == 200, response.text
    return {"site": site, "drive": drive, "root": root, "item": item, "blob": blob, "batch": batch}


async def site_grant(client, tenant, site, principal, kind="user", role="read"):
    response = await client.post(
        f"/_mock/tenants/{tenant}/workloads/sharepoint/grants",
        json={"scope": site, "principal_kind": kind, "principal_id": str(principal), "role": role},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]
