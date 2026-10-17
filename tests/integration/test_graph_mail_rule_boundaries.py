from datetime import UTC, datetime, timedelta

from tests.integration.graph_support import graph_client, headers_for, registered_app
from tests.integration.test_graph_mail_rules import body
from tests.integration.test_mailbox_state import provision


async def test_rule_continuation_is_bound_and_expired(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(client, tenant, user)
        headers = await headers_for(
            database, settings, tenant, app, None, {"MailboxSettings.ReadWrite"}
        )
        path = f"/v1.0/users/{user}/mailFolders/inbox/messageRules"
        for index in range(3):
            assert (
                await client.post(path, headers=headers, json=body(str(index), index + 1))
            ).status_code == 201
        response = await client.get(path + "?$top=1", headers=headers)
        assert response.status_code == 200, response.text
        link = response.json()["@odata.nextLink"]
        other_app = await registered_app(client, tenant)
        other_headers = await headers_for(
            database, settings, tenant, other_app, None, {"MailboxSettings.ReadWrite"}
        )
        assert (await client.get(link, headers=other_headers)).status_code == 400
        assert (
            await client.get(link.replace("/v1.0/", "/beta/"), headers=headers)
        ).status_code == 400
        app_instance = client._transport.app
        app_instance.state.clock = lambda: datetime.now(UTC) + timedelta(hours=2)
        assert (await client.get(link, headers=headers)).status_code == 400


async def test_rule_validation_is_atomic_and_query_options_are_strict(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(client, tenant, user)
        headers = await headers_for(
            database, settings, tenant, app, None, {"MailboxSettings.ReadWrite"}
        )
        path = f"/v1.0/users/{user}/mailFolders/inbox/messageRules"
        created = await client.post(path, headers=headers, json=body())
        assert created.status_code == 201, created.text
        original = created.json()
        item = path + "/" + original["id"]
        for invalid in (
            {"actions": {"forwardTo": [{"emailAddress": {}}]}},
            {"actions": {"forwardTo": [{"emailAddress": None}]}},
            {"conditions": {"withinSizeRange": {"minimumSize": 10, "maximumSize": 1}}},
            {"actions": {"moveToFolder": None}},
            {"sequence": -2},
            {"conditions": {"unknownField": True}},
            {"hasError": True},
        ):
            response = await client.patch(item, headers=headers, json=invalid)
            assert response.status_code == 400, response.text
            assert (await client.get(item, headers=headers)).json() == original
        for option in ("$top=0", "$top=1001", "$top=2&$top=3", "$select=missing", "$select=id,id"):
            assert (await client.get(path + "?" + option, headers=headers)).status_code == 400
        readonly = await headers_for(
            database, settings, tenant, app, None, {"MailboxSettings.Read"}
        )
        assert (await client.get(item, headers=readonly)).status_code == 200
        assert (await client.delete(item, headers=readonly)).status_code == 403
