from tests.integration.identity_support import identity_client
from tests.integration.test_population_runs import request_for


async def test_population_api_validates_inputs_and_freezes_bundle_revisions(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        builtin = await client.get("/_mock/profiles/builtin")
        assert builtin.status_code == 200
        saved = await client.post(
            "/_mock/profiles", json={"documents": builtin.json()["documents"]}
        )
        assert saved.status_code == 201
        identifier = saved.json()["id"]
        update = await client.put(
            f"/_mock/profiles/{identifier}?expected_revision=1",
            json={"documents": builtin.json()["documents"]},
        )
        assert update.status_code == 200
        stale = await client.put(
            f"/_mock/profiles/{identifier}?expected_revision=1",
            json={"documents": builtin.json()["documents"]},
        )
        assert stale.status_code == 409
        invalid = await client.post(
            "/_mock/population/previews", json={"target": {"url": "https://graph.microsoft.com"}}
        )
        assert invalid.status_code == 422
        p = await client.post(
            "/_mock/population/previews", json=request_for(tenant).model_dump(mode="json")
        )
        assert p.status_code == 201, p.text
        assert all(len(v) <= 10 for v in p.json()["samples"].values())
        created = await client.post(
            "/_mock/population/runs",
            json={"preview_id": p.json()["id"], "digest": p.json()["digest"]},
        )
        assert created.status_code == 201, created.text
        status = await client.get("/_mock/population/runs/" + created.json()["id"])
        assert status.json()["state"] == "pending"
        assert "encrypted" not in status.text
