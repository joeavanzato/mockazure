from uuid import uuid4

import pytest

from mockazure.security.models import SecurityObject
from mockazure.workloads.intune import device_observation
from tests.integration.graph_support import graph_client
from tests.integration.intune_support import import_records, license_intune, record


@pytest.mark.parametrize(
    "kind,derived",
    [
        ("deviceConfiguration", "windows10GeneralConfiguration"),
        ("deviceCompliancePolicy", "windows10CompliancePolicy"),
    ],
)
@pytest.mark.parametrize("annotation", [None, 42, [], {}])
async def test_invalid_policy_annotation_is_validation_error(
    database, settings, kind, derived, annotation
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await license_intune(client, tenant)
        row = record(kind, "bad", derived=derived)
        row["payload"]["@odata.type"] = annotation
        response = await import_records(client, tenant, [row])
        assert response.status_code == 422, response.text


@pytest.mark.parametrize("representation", ["hex", "upper", "braces", "urn"])
async def test_accepted_device_id_form_remains_observable(database, settings, representation):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await license_intune(client, tenant)
        device_id = uuid4()
        value = {
            "hex": device_id.hex,
            "upper": str(device_id).upper(),
            "braces": "{" + str(device_id) + "}",
            "urn": device_id.urn,
        }[representation]
        async with database.sessions.begin() as session:
            session.add(
                SecurityObject(
                    tenant_id=tenant,
                    id=uuid4(),
                    kind="device",
                    properties={"deviceId": str(device_id), "accountEnabled": True},
                )
            )
        response = await import_records(
            client,
            tenant,
            [record("managedDevice", "m", azureADDeviceId=value, complianceState="compliant")],
        )
        assert response.status_code == 200, response.text
        async with database.sessions() as session:
            observation = await device_observation(session, tenant, device_id)
            assert observation is not None
            assert observation.device_id == device_id
            assert observation.compliant is True
