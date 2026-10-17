from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from mockazure.evidence.models import EvidenceObservation, EvidenceVersion
from mockazure.providers.graph.schema_reference import validate_payload
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_graph_evidence_reads import publish, record
from tests.integration.test_graph_licensing import seed_sku, set_role


async def test_registration_inventory_filters_and_authorization(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant)
        auth = await headers_for(database, settings, tenant, app, None, {"AuditLog.Read.All"})
        path = "/v1.0/reports/authenticationMethods/userRegistrationDetails"
        response = await client.get(path, headers=auth)
        assert response.status_code == 200, response.text
        own = next(v for v in response.json()["value"] if v["id"] == str(user))
        assert own["methodsRegistered"] == [] and own["isMfaRegistered"] is False
        assert own["isMfaCapable"] is False and own["isSsprEnabled"] is False
        method = await client.post(
            f"/_mock/tenants/{tenant}/security/users/{user}/authenticationMethods",
            json={
                "@odata.type": "#microsoft.graph.phoneAuthenticationMethod",
                "phoneType": "mobile",
                "phoneNumber": "+1 5555550135",
            },
        )
        assert method.status_code == 201
        response = await client.get(
            path, headers=auth, params={"$filter": "isMfaRegistered eq true"}
        )
        assert response.status_code == 200
        own = next(v for v in response.json()["value"] if v["id"] == str(user))
        assert own["methodsRegistered"] == ["mobilePhone"]
        validate_payload("v1.0", "userRegistrationDetails", own)
        item = (await client.get(path + "/" + str(user), headers=auth)).json()
        assert {k: v for k, v in item.items() if k != "lastUpdatedDateTime"} == {
            k: v for k, v in own.items() if k != "lastUpdatedDateTime"
        }
        assert item["lastUpdatedDateTime"] >= own["lastUpdatedDateTime"]
        for expression in (
            "methodsRegistered/any(x:x eq 'mobilePhone')",
            "startswith(userPrincipalName,'READER')",
        ):
            result = await client.get(path, headers=auth, params={"$filter": expression})
            assert result.status_code == 200 and len(result.json()["value"]) == 1
        denied = await headers_for(database, settings, tenant, app, None, {"User.Read.All"})
        assert (await client.get(path, headers=denied)).status_code == 403
        delegated = await headers_for(
            database, settings, tenant, app, user, {"AuditLog.Read.All"}, "delegated"
        )
        assert (await client.get(path, headers=delegated)).status_code == 403
        await set_role(database, tenant, user, "Reports Reader")
        assert (await client.get(path, headers=delegated)).status_code == 200
        assert (await client.get(path, headers=auth, params={"$select": "id"})).status_code == 400
        control = f"/_mock/tenants/{tenant}/identity/users/{user}"
        assert (await client.patch(control, json={"enabled": False})).status_code == 200
        assert (await client.get(path + "/" + str(user), headers=auth)).status_code == 404


async def test_signin_activity_attempts_success_retention_and_disclosure(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant)
        auth = await headers_for(
            database, settings, tenant, app, None, {"User.Read.All", "AuditLog.Read.All"}
        )
        now = datetime.now(UTC) - timedelta(hours=2)
        values = [
            record("signIn", user, now),
            record("signIn", user, now + timedelta(minutes=2), event="nonInteractiveUser"),
            record(
                "signIn",
                user,
                now + timedelta(minutes=3),
                status={"errorCode": 50126, "failureReason": "Bad password"},
            ),
            record("signIn", user, now - timedelta(minutes=1)),
        ]
        for value in values:
            await publish(
                database, tenant, "signIn", value, datetime.fromisoformat(value["createdDateTime"])
            )
        path = "/v1.0/users/" + str(user)
        response = await client.get(path, headers=auth, params={"$select": "Id,SignInActivity"})
        assert response.status_code == 200, response.text
        activity = response.json()["signInActivity"]
        assert activity["lastSignInRequestId"] == values[2]["id"]
        assert activity["lastNonInteractiveSignInRequestId"] == values[1]["id"]
        assert activity["lastSuccessfulSignInRequestId"] == values[1]["id"]
        validate_payload("v1.0", "signInActivity", activity)
        async with database.sessions.begin() as session:
            await session.execute(
                delete(EvidenceVersion).where(EvidenceVersion.tenant_id == tenant)
            )
            await session.execute(
                delete(EvidenceObservation).where(EvidenceObservation.tenant_id == tenant)
            )
        retained = await client.get(path, headers=auth, params={"$select": "signInActivity"})
        assert retained.json()["signInActivity"] == activity
        assert "signInActivity" not in (await client.get(path, headers=auth)).json()
        denied = await headers_for(database, settings, tenant, app, None, {"User.Read.All"})
        assert (
            await client.get(path, headers=denied, params={"$select": "signInActivity"})
        ).status_code == 403
        collection = await client.get(
            "/v1.0/users", headers=auth, params={"$select": "id,signInActivity"}
        )
        assert collection.status_code == 200
        assert (
            next(v for v in collection.json()["value"] if v["id"] == str(user))["signInActivity"]
            == activity
        )
