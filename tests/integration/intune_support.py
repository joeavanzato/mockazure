from tools.intune_fixtures import payload

INTUNE_SKU = "061f9ace-7d42-4136-88ac-31dc755f143f"
INTUNE_PLAN = "c1ec4a95-1f05-45b3-a911-aa3fa01094f5"


async def license_intune(client, tenant, enabled=10):
    response = await client.put(
        f"/_mock/tenants/{tenant}/licensing/skus/{INTUNE_SKU}",
        json={
            "sku_part_number": "INTUNE_A",
            "status": "Enabled",
            "enabled": enabled,
            "service_plans": [
                {
                    "servicePlanId": INTUNE_PLAN,
                    "servicePlanName": "INTUNE_A",
                    "provisioningStatus": "Success",
                    "appliesTo": "User",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text


async def import_records(client, tenant, records, edges=None, tombstones=None):
    return await client.post(
        f"/_mock/tenants/{tenant}/workloads/intune/import",
        json={
            "scope": "deviceManagement",
            "records": records,
            "edges": edges or [],
            "tombstones": tombstones or [],
        },
    )


def record(kind, identifier, **values):
    actual = values.pop("derived", kind)
    return {"kind": kind, "id": identifier, "payload": payload(actual, identifier, **values)}


def edge(sk, sid, relation, tk, tid):
    return {
        "source": {"kind": sk, "id": sid},
        "relation": relation,
        "target": {"kind": tk, "id": tid},
    }
