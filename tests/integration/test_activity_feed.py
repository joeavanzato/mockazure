from uuid import uuid4

from tests.integration.graph_support import graph_client, headers_for, registered_app


async def test_feed_audience_tenant_client_and_subscription_boundaries(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path = f"/api/v1.0/{tenant}/activity/feed"
        graph = await headers_for(database, settings, tenant, app, None, {"ActivityFeed.Read"})
        assert (await client.get(path + "/subscriptions/list", headers=graph)).status_code == 401
        auth = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {"ActivityFeed.Read"},
            audience="https://manage.office.com",
        )
        assert (
            await client.get(
                path.replace(str(tenant), str(uuid4())) + "/subscriptions/list", headers=auth
            )
        ).status_code == 400
        url = path + "/subscriptions/start?contentType=Audit.Exchange"
        for _ in range(2):
            result = await client.post(url, headers=auth)
            assert result.status_code == 200, result.text
            assert result.json() == {
                "contentType": "Audit.Exchange",
                "status": "enabled",
                "webhook": None,
            }
        assert len((await client.get(path + "/subscriptions/list", headers=auth)).json()) == 1
        other = await registered_app(client, tenant)
        other_auth = await headers_for(
            database,
            settings,
            tenant,
            other,
            None,
            {"ActivityFeed.Read"},
            audience="https://manage.office.com",
        )
        assert (await client.get(path + "/subscriptions/list", headers=other_auth)).json() == []
        assert (
            await client.post(
                url, headers=auth, json={"webhook": {"address": "https://localhost:1"}}
            )
        ).status_code == 400
        assert (
            await client.post(path + "/subscriptions/start?contentType=DLP.All", headers=auth)
        ).status_code == 403
        assert (
            await client.post(path + "/subscriptions/stop?contentType=Audit.Exchange", headers=auth)
        ).status_code == 200
        result = await client.get(
            path + "/subscriptions/content?contentType=Audit.Exchange", headers=auth
        )
        assert result.status_code == 400 and result.json()["error"]["code"] == "AF20023"
