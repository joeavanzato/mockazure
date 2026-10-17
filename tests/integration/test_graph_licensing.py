import asyncio
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select

from mockazure.directory.models import EvidenceOutbox, ObjectVersion
from mockazure.identity.models import DirectoryRole
from mockazure.providers.graph.schema_reference import validate_payload
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_evidence_directory import drain, graph_audits
from tests.integration.test_graph_delta import finish
from tests.integration.test_graph_mutations import WRITE, user_body

P1 = "078d2b04-f1bd-4111-bbd4-b4b1b354cef4"
P2 = "84a661c4-e949-4bd2-a560-ed7766fcaf2b"
PLAN1 = "41781fb2-bc02-4b7c-bd55-b576c07bb09d"
PLAN2 = "eec0eb4f-6444-4f95-aba0-50c24d67f998"


def sku_input(plan=PLAN2, seats=10):
    return {
        "sku_part_number": "AAD_PREMIUM_P2",
        "status": "Enabled",
        "enabled": seats,
        "suspended": 0,
        "warning": 0,
        "service_plans": [
            {
                "servicePlanId": plan,
                "servicePlanName": "AAD_PREMIUM_P2",
                "provisioningStatus": "Success",
                "appliesTo": "User",
            }
        ],
        "allowed_usage_locations": ["US", "GB"],
    }


async def seed_sku(client, tid, sku=P2, plan=PLAN2, seats=10):
    response = await client.put(
        f"/_mock/tenants/{tid}/licensing/skus/{sku}", json=sku_input(plan, seats)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def new_user(client, headers, name, location="US"):
    response = await client.post(
        "/v1.0/users", headers=headers, json={**user_body(name), "usageLocation": location}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def assign(sku=P2, disabled=()):
    return {"addLicenses": [{"skuId": sku, "disabledPlans": list(disabled)}], "removeLicenses": []}


async def set_role(database, tid, uid, role):
    async with database.sessions.begin() as session:
        await session.execute(
            delete(DirectoryRole).where(
                DirectoryRole.tenant_id == tid, DirectoryRole.user_id == uid
            )
        )
        if role:
            session.add(DirectoryRole(tenant_id=tid, user_id=uid, role=role))


async def test_license_api_read_write_matrix(database, settings):
    async with graph_client(database, settings) as (client, tid, app, actor):
        await seed_sku(client, tid)
        write = await headers_for(database, settings, tid, app, None, WRITE)
        user = await new_user(client, write, "licensed")
        subscribed = {
            "LicenseAssignment.Read.All",
            "Directory.Read.All",
            "Directory.ReadWrite.All",
            "Organization.Read.All",
            "Organization.ReadWrite.All",
        }
        for kind, roles in [
            ("application", [None]),
            (
                "delegated",
                [
                    "Global Reader",
                    "Directory Readers",
                    "Dynamics 365 Business Central Administrator",
                    "Global Administrator",
                ],
            ),
        ]:
            for role in roles:
                await set_role(database, tid, actor, role)
                for scope in subscribed:
                    h = await headers_for(
                        database,
                        settings,
                        tid,
                        app,
                        actor if kind == "delegated" else None,
                        {scope},
                        kind=kind,
                    )
                    response = await client.get(
                        "/v1.0/subscribedSkus?$select=skuId,consumedUnits", headers=h
                    )
                    assert response.status_code == 200, (scope, role, response.text)
                    value = response.json()["value"][0]
                    assert set(value) == {"id", "@odata.type", "skuId", "consumedUnits"}
                    assert value["skuId"] == P2
        detail_scopes = {
            "LicenseAssignment.Read.All",
            "Directory.Read.All",
            "Directory.ReadWrite.All",
            "User.Read.All",
            "User.ReadWrite.All",
        }
        roles = [
            "Guest Inviter",
            "Directory Readers",
            "Directory Writers",
            "License Administrator",
            "User Administrator",
            "Global Administrator",
        ]
        for role in roles:
            await set_role(database, tid, actor, role)
            for scope in detail_scopes:
                h = await headers_for(
                    database, settings, tid, app, actor, {scope}, kind="delegated"
                )
                assert (
                    await client.get(f"/v1.0/users/{user}/licenseDetails", headers=h)
                ).status_code == 200
            h = await headers_for(
                database, settings, tid, app, actor, {"User.Read"}, kind="delegated"
            )
            assert (await client.get("/v1.0/me/licenseDetails", headers=h)).status_code == 200
            assert (
                await client.get(f"/v1.0/users/{user}/licenseDetails", headers=h)
            ).status_code == 403
        for scope in detail_scopes:
            h = await headers_for(database, settings, tid, app, None, {scope})
            assert (
                await client.get(f"/v1.0/users/{user}/licenseDetails", headers=h)
            ).status_code == 403
        for kind, roles in [
            ("application", [None]),
            (
                "delegated",
                [
                    "Directory Writers",
                    "License Administrator",
                    "User Administrator",
                    "Global Administrator",
                ],
            ),
        ]:
            for role in roles:
                await set_role(database, tid, actor, role)
                for scope in {
                    "LicenseAssignment.ReadWrite.All",
                    "Directory.ReadWrite.All",
                    "User.ReadWrite.All",
                }:
                    h = await headers_for(
                        database,
                        settings,
                        tid,
                        app,
                        actor if kind == "delegated" else None,
                        {scope},
                        kind=kind,
                    )
                    assert (
                        await client.post(
                            f"/v1.0/users/{user}/assignLicense", headers=h, json=assign()
                        )
                    ).status_code == 200
        await set_role(database, tid, actor, None)
        denied = await headers_for(
            database, settings, tid, app, actor, {"LicenseAssignment.Read.All"}, kind="delegated"
        )
        assert (await client.get("/v1.0/subscribedSkus", headers=denied)).status_code == 403
        h = await headers_for(
            database, settings, tid, app, None, {"LicenseAssignment.ReadWrite.All"}
        )
        assert (
            await client.post(f"/v1.0/users/{uuid4()}/assignLicense", headers=h, json=assign())
        ).status_code == 404


async def test_assignment_updates_inventory_delta_and_entitlements(database, settings):
    async with graph_client(database, settings) as (client, tid, app, actor):
        await seed_sku(client, tid)
        headers = await headers_for(
            database, settings, tid, app, None, WRITE | {"LicenseAssignment.Read.All"}
        )
        uid = await new_user(client, headers, "delta")
        _, link = await finish(
            client,
            headers,
            await client.get(
                "/v1.0/users/delta?$select=id,assignedLicenses,assignedPlans", headers=headers
            ),
        )
        response = await client.post(
            f"/v1.0/users/{uid}/assignLicense", headers=headers, json=assign()
        )
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["assignedLicenses"] == [{"skuId": P2, "disabledPlans": []}]
        validate_payload("v1.0", "assignedLicense", value["assignedLicenses"][0])
        validate_payload("v1.0", "assignedPlan", value["assignedPlans"][0])
        values, link = await finish(client, headers, await client.get(link, headers=headers))
        assert len(values) == 1 and values[0]["id"] == uid
        assert values[0]["assignedLicenses"] == value["assignedLicenses"]
        assert (
            await client.post(f"/v1.0/users/{uid}/assignLicense", headers=headers, json=assign())
        ).status_code == 200
        values, link = await finish(client, headers, await client.get(link, headers=headers))
        assert values == []
        from mockazure.licensing.service import entitlements

        async with database.sessions() as session:
            effective = await entitlements(session, tid, UUID(uid))
            assert effective.tenant_tier == "P2" and UUID(PLAN2) in effective.user_plan_ids
        inventory = (await client.get("/v1.0/subscribedSkus", headers=headers)).json()["value"][0]
        validate_payload(
            "v1.0", "subscribedSku", {k: v for k, v in inventory.items() if not k.startswith("@")}
        )
        assert inventory["consumedUnits"] == 1
        await set_role(database, tid, actor, "License Administrator")
        delegate = await headers_for(
            database, settings, tid, app, actor, {"LicenseAssignment.Read.All"}, kind="delegated"
        )
        detail = (
            await client.get("/v1.0/users/delta@contoso.test/licenseDetails", headers=delegate)
        ).json()["value"][0]
        validate_payload(
            "v1.0", "licenseDetails", {k: v for k, v in detail.items() if not k.startswith("@")}
        )
        assert detail["skuId"] == P2 and detail["servicePlans"][0]["servicePlanId"] == PLAN2
        assert (
            await client.post(
                f"/v1.0/users/{uid}/assignLicense", headers=headers, json=assign(disabled=[PLAN2])
            )
        ).status_code == 200
        async with database.sessions() as session:
            effective = await entitlements(session, tid, UUID(uid))
            assert effective.tenant_tier == "P2" and not effective.user_plan_ids
        assert (
            await client.post(
                f"/v1.0/users/{uid}/assignLicense", headers=headers, json={"removeLicenses": [P2]}
            )
        ).status_code == 200
        assert (await client.get("/v1.0/subscribedSkus", headers=headers)).json()["value"][0][
            "consumedUnits"
        ] == 0
        exported = await client.get(f"/_mock/tenants/{tid}/directory/export")
        exported_user = next(u for u in exported.json()["users"] if u["id"] == uid)
        assert not {"usageLocation", "assignedLicenses", "assignedPlans"} & exported_user.keys()
    await drain(database)
    audits = await graph_audits(database)
    assert sum(a["activityDisplayName"] == "Change user license" for a, _ in audits) == 3


async def test_last_seat_concurrency_and_delete_release(database, settings):
    async with graph_client(database, settings) as (client, tid, app, actor):
        await seed_sku(client, tid, seats=1)
        headers = await headers_for(
            database, settings, tid, app, None, WRITE | {"LicenseAssignment.Read.All"}
        )
        users = [await new_user(client, headers, name) for name in ("seat1", "seat2")]
        responses = await asyncio.gather(
            *(
                client.post(f"/v1.0/users/{uid}/assignLicense", headers=headers, json=assign())
                for uid in users
            )
        )
        assert sorted(r.status_code for r in responses) == [200, 400]
        winner = users[next(i for i, r in enumerate(responses) if r.status_code == 200)]
        loser = next(u for u in users if u != winner)
        shrink = await client.put(
            f"/_mock/tenants/{tid}/licensing/skus/{P2}", json=sku_input(seats=0)
        )
        assert shrink.status_code == 409
        assert (await client.delete(f"/v1.0/users/{winner}", headers=headers)).status_code == 204
        assert (
            await client.post(f"/v1.0/users/{loser}/assignLicense", headers=headers, json=assign())
        ).status_code == 200
        assert (await client.get("/v1.0/subscribedSkus", headers=headers)).json()["value"][0][
            "consumedUnits"
        ] == 1


async def test_assignment_atomic_validation(database, settings):
    async with graph_client(database, settings) as (client, tid, app, actor):
        await seed_sku(client, tid)
        await seed_sku(client, tid, sku=P1, plan=PLAN2)
        headers = await headers_for(database, settings, tid, app, None, WRITE)
        uid = await new_user(client, headers, "atomic")

        async def counts():
            async with database.sessions() as session:
                return tuple(
                    [
                        await session.scalar(select(func.count()).select_from(m))
                        for m in (ObjectVersion, EvidenceOutbox)
                    ]
                )

        baseline = await counts()
        for body in [
            assign(str(uuid4())),
            assign(disabled=[str(uuid4())]),
            {"addLicenses": [{"skuId": P2}, {"skuId": P2}], "removeLicenses": []},
            {"addLicenses": [{"skuId": P2}], "removeLicenses": [P2]},
            {"addLicenses": [{"skuId": P2}]},
        ]:
            response = await client.post(
                f"/v1.0/users/{uid}/assignLicense", headers=headers, json=body
            )
            assert response.status_code == 400, response.text
            assert await counts() == baseline
        assert (
            await client.patch(f"/v1.0/users/{uid}", headers=headers, json={"usageLocation": "ZZ"})
        ).status_code == 400
        assert (
            await client.patch(f"/v1.0/users/{uid}", headers=headers, json={"usageLocation": "CA"})
        ).status_code == 204
        assert (
            await client.post(f"/v1.0/users/{uid}/assignLicense", headers=headers, json=assign())
        ).status_code == 400
        assert (
            await client.patch(f"/v1.0/users/{uid}", headers=headers, json={"usageLocation": "US"})
        ).status_code == 204
        async with database.sessions.begin() as session:
            session.add(
                DirectoryRole(tenant_id=tid, user_id=UUID(uid), role="Global Administrator")
            )
        assert (
            await client.post(f"/v1.0/users/{uid}/assignLicense", headers=headers, json=assign())
        ).status_code == 400
        await set_role(database, tid, UUID(uid), None)
        for body in (assign(disabled=[PLAN2]), assign(P1)):
            assert (
                await client.post(f"/v1.0/users/{uid}/assignLicense", headers=headers, json=body)
            ).status_code == 200
        from mockazure.licensing.service import entitlements

        async with database.sessions() as session:
            assert UUID(PLAN2) in (await entitlements(session, tid, UUID(uid))).user_plan_ids


async def test_inventory_policy_refresh_and_schema_bounds(database, settings):
    async with graph_client(database, settings) as (client, tid, app, actor):
        await seed_sku(client, tid)
        await seed_sku(client, tid, sku=P1, plan=PLAN1)
        h = await headers_for(database, settings, tid, app, None, WRITE)
        uid = await new_user(client, h, "policy")
        assert (
            await client.post(f"/v1.0/users/{uid}/assignLicense", headers=h, json=assign())
        ).status_code == 200
        changed = sku_input()
        changed["status"] = "Suspended"
        assert (
            await client.put(f"/_mock/tenants/{tid}/licensing/skus/{P2}", json=changed)
        ).status_code == 200
        value = (await client.get(f"/v1.0/users/{uid}?$select=assignedPlans", headers=h)).json()
        assert value["assignedPlans"][0]["capabilityStatus"] == "Suspended"
        from mockazure.licensing.service import entitlements

        async with database.sessions() as session:
            current = await entitlements(session, tid, UUID(uid))
            assert current.tenant_tier == "P1" and not current.user_plan_ids
        many = sku_input()
        many["service_plans"] = [
            dict(
                many["service_plans"][0],
                servicePlanId=str(uuid4()),
                servicePlanName="Plan" + str(i),
            )
            for i in range(100)
        ]
        large_id = str(uuid4())
        assert (
            await client.put(f"/_mock/tenants/{tid}/licensing/skus/{large_id}", json=many)
        ).status_code == 200
        response = await client.post(
            f"/v1.0/users/{uid}/assignLicense", headers=h, json=assign(large_id)
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["assignedPlans"]) == 101
        second_id = str(uuid4())
        for plan in many["service_plans"]:
            plan["servicePlanId"] = str(uuid4())
        assert (
            await client.put(f"/_mock/tenants/{tid}/licensing/skus/{second_id}", json=many)
        ).status_code == 200
        response = await client.post(
            f"/v1.0/users/{uid}/assignLicense", headers=h, json=assign(second_id)
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["assignedPlans"]) == 201
        await drain(database)
        async with database.sessions() as session:
            from mockazure.db.job_model import Job

            assert (
                await session.scalar(
                    select(func.count()).select_from(Job).where(Job.state == "failed")
                )
                == 0
            )


async def test_qualified_assign_license_action(database, settings):
    async with graph_client(database, settings) as (client, tid, app, actor):
        await seed_sku(client, tid)
        write = await headers_for(database, settings, tid, app, None, WRITE)
        user = await new_user(client, write, "qualified-license")
        response = await client.post(
            f"/v1.0/users/{user}/microsoft.graph.assignLicense",
            headers=write,
            json=assign(),
        )
        assert response.status_code == 200
        assert response.json()["assignedLicenses"][0]["skuId"] == P2
