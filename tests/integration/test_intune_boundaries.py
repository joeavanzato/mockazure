from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from mockazure.db.migrations import configuration
from mockazure.db.models import Tenant
from tests.integration.graph_support import graph_client, headers_for, registered_app
from tests.integration.intune_support import import_records, license_intune, record


async def test_projection_query_rejection_and_cursor_binding(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        assert (
            await import_records(client, tenant, [record("managedDevice", "missing-license")])
        ).status_code == 403
        await license_intune(client, tenant)
        rows = [
            record(
                "deviceConfiguration",
                f"p{i}",
                derived="windows10GeneralConfiguration",
                displayName=f"Policy {i}",
                defenderScheduledScanTime="05:30:00",
            )
            for i in range(3)
        ]
        assert (await import_records(client, tenant, rows)).status_code == 200
        auth = await headers_for(
            database, settings, tenant, app, None, {"DeviceManagementConfiguration.Read.All"}
        )
        result = await client.get(
            "/v1.0/deviceManagement/deviceConfigurations?$select=id,defenderScheduledScanTime&$top=1",
            headers=auth,
        )
        assert result.status_code == 200, result.text
        assert result.json()["value"][0] == {
            "id": "p0",
            "@odata.type": "#microsoft.graph.windows10GeneralConfiguration",
            "defenderScheduledScanTime": "05:30:00",
        }
        link = result.json()["@odata.nextLink"]
        other = await registered_app(client, tenant)
        other_auth = await headers_for(
            database, settings, tenant, other, None, {"DeviceManagementConfiguration.Read.All"}
        )
        assert (await client.get(link, headers=other_auth)).status_code == 400
        for query in (
            "$expand=assignments",
            "$search=Policy",
            "$filter=madeUp eq 'x'",
            "$orderby=madeUp",
            "$top=0",
            "$top=1001",
            "$top=1&$top=2",
        ):
            result = await client.get(
                "/v1.0/deviceManagement/deviceConfigurations?" + query, headers=auth
            )
            assert result.status_code == 400, result.text
        result = await client.get(
            "/v1.0/deviceManagement/deviceConfigurations?$filter=displayName eq 'Policy 1'",
            headers=auth,
        )
        assert [v["id"] for v in result.json()["value"]] == ["p1"]
        assert (
            await client.post("/v1.0/deviceManagement/managedDevices/m/wipe", headers=auth, json={})
        ).status_code == 405


def test_intune_upgrade_preserves_prior_data_and_refuses_lossy_downgrade(settings):
    engine = create_engine(settings.database_url.get_secret_value())
    tenant = uuid4()
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "047")
            connection.execute(
                Tenant.__table__.insert().values(
                    id=tenant,
                    slug="intune-upgrade",
                    display_name="Keep",
                    domain="intune-upgrade.test",
                    licences=[],
                    revision=9,
                )
            )
            command.upgrade(config, "head")
            assert (
                connection.scalar(text("SELECT revision FROM tenants WHERE id=:id"), {"id": tenant})
                == 9
            )
            connection.execute(
                text(
                    "INSERT INTO workload_scopes(tenant_id,workload,scope,revision,generation,"
                    "grant_revision) VALUES(:id,'intune','deviceManagement',0,:generation,0)"
                ),
                {"id": tenant, "generation": uuid4()},
            )
        with pytest.raises(DBAPIError, match="Export and clear Intune"):
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "047")
        with engine.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM workload_scopes WHERE workload='intune'")
                )
                == 1
            )
            connection.execute(text("DELETE FROM workload_scopes WHERE workload='intune'"))
            config.attributes["connection"] = connection
            command.downgrade(config, "047")
            command.upgrade(config, "head")
    finally:
        engine.dispose()
