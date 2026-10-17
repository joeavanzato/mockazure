"""Prepare only this checkout's isolated M3B runtime and synthetic directory."""

import argparse
import json
import secrets
import ssl
import sys
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import httpx

from mockazure.bootstrap import initialize_instance
from mockazure.files import write_private
from mockazure.tls import renew_leaf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.qualification_profile import profile_for  # noqa: E402

PROFILE = profile_for("m3b")
RUNTIME = ROOT / PROFILE.text(".runtime/m3b")
INSTANCE = RUNTIME / "instance"
NAMESPACE = UUID("8e7dfba0-6443-4f71-8658-929bc5374769")
PERMISSIONS = [
    "User.Read.All",
    "User.ReadWrite.All",
    "User.EnableDisableAccount.All",
    "Group.ReadWrite.All",
    "Directory.Read.All",
    "Member.Read.Hidden",
    "AuditLog.Read.All",
    "IdentityRiskyUser.Read.All",
    "IdentityRiskyUser.ReadWrite.All",
    "IdentityRiskEvent.Read.All",
    "LicenseAssignment.ReadWrite.All",
    "LicenseAssignment.Read.All",
    "Organization.Read.All",
]
CONTAINER = {
    "login": "https://login.mockazure.test",
    "graph": "https://graph.mockazure.test",
    "exchange": "https://outlook.mockazure.test",
    "arm": "https://management.mockazure.test",
    "browser_login": PROFILE.text("https://localhost:51443"),
}
HOST = {name: PROFILE.text("https://localhost:51443") for name in CONTAINER}


def save(path, data):
    contents = (json.dumps(data, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.write_bytes(contents)
    else:
        write_private(path, contents)


def initialize():
    if not (INSTANCE / "native.json").exists():
        import socket

        for port in (
            PROFILE.port(51443),
            PROFILE.port(55436),
            PROFILE.port(51444),
            PROFILE.port(51445),
            PROFILE.port(51446),
        ):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
    RUNTIME.mkdir(parents=True, exist_ok=True)
    secret = RUNTIME / "db.password"
    if not secret.exists():
        write_private(secret, (secrets.token_urlsafe(32) + "\n").encode())
    password = secret.read_text().strip()
    files = initialize_instance(
        INSTANCE,
        database_url=PROFILE.text(
            f"postgresql://mockazure_m3b:{password}@127.0.0.1:55436/mockazure_m3b"
        ),
        advertised_host="localhost",
        port=PROFILE.port(51443),
    )
    config = json.loads(files.config_file.read_text())
    aliases = [
        "localhost",
        "127.0.0.1",
        "login.mockazure.test",
        "graph.mockazure.test",
        "outlook.mockazure.test",
        "management.mockazure.test",
        "identity.mockazure.test",
    ]
    config.update(
        allowed_hosts=aliases,
        service_endpoints=CONTAINER,
        client_profiles={"host": HOST, "container": CONTAINER},
        managed_identity_listen_host="127.0.0.1",
        managed_identity_listen_port=PROFILE.port(51446),
    )
    save(files.config_file, config)
    config.update(
        listen_host="0.0.0.0",
        listen_port=8443,
        managed_identity_listen_host="0.0.0.0",
        managed_identity_listen_port=8080,
        database_url=PROFILE.text(
            f"postgresql+psycopg://mockazure_m3b:{password}@db:5432/mockazure_m3b"
        ),
    )
    if not (INSTANCE / "container.json").exists():
        renew_leaf(INSTANCE, aliases)
    save(INSTANCE / "container.json", config)
    for language in ("python", "powershell", "native"):
        for folder in ("state", "root", "tmp"):
            (RUNTIME / "clients" / f"{language}-{folder}").mkdir(parents=True, exist_ok=True)
    (RUNTIME / "api-state").mkdir(exist_ok=True)
    print(PROFILE.text("M3B configuration ready under .runtime/m3b; credentials retained locally"))


def checked(response, expected=200):
    if response.status_code != expected:
        raise RuntimeError(
            f"M3B preparation request failed ({response.status_code}, expected {expected})"
        )
    return response.json() if response.content else None


def seed_dataset():
    users = [
        {
            "id": str(uuid5(NAMESPACE, "user-" + str(i))),
            "displayName": f"Seed Analyst {i:05d}",
            "userPrincipalName": f"seed{i:05d}@graph.test",
            "mailNickname": f"seed{i:05d}",
            "accountEnabled": True,
            "createdDateTime": "2026-10-01T00:00:00Z",
        }
        for i in range(5000)
    ]
    return {
        "schema_version": 1,
        "users": users,
        "groups": [],
        "relationships": [],
        "provenance": {"source": "MockAzure M3B qualification", "seed": 1},
    }


def provision():
    with httpx.Client(
        base_url=HOST["graph"],
        verify=ssl.create_default_context(cafile=str(INSTANCE / "ca.pem")),
        trust_env=False,
        timeout=120,
        headers={"Authorization": "Bearer " + (INSTANCE / "admin.token").read_text().strip()},
    ) as client:
        record = RUNTIME / "provisioning.json"
        seed = seed_dataset()
        save(RUNTIME / "seed.json", seed)
        if record.exists():
            state = json.loads(record.read_text())
            checked(client.get(f"/_mock/tenants/{state['tenant_id']}"))
        else:

            def post(path, data):
                return checked(client.post(path, json=data), 201)

            def put(path, data):
                return checked(client.put(path, json=data))

            tenant = post(
                "/_mock/tenants",
                {
                    "slug": "graph-lab",
                    "display_name": "Graph Lab",
                    "domain": "graph.test",
                    "licences": ["E5"],
                },
            )
            foreign = post(
                "/_mock/tenants",
                {
                    "slug": "graph-foreign",
                    "display_name": "Foreign Lab",
                    "domain": "foreign-graph.test",
                    "licences": [],
                },
            )
            base = f"/_mock/tenants/{tenant['id']}/identity"
            user_password = secrets.token_urlsafe(24)
            user = post(
                base + "/users",
                {
                    "upn": "sdk-admin@graph.test",
                    "display_name": "SDK Admin",
                    "password": user_password,
                },
            )
            put(base + f"/users/{user['id']}/roles/Global Administrator", {})
            foreign_user = post(
                f"/_mock/tenants/{foreign['id']}/identity/users",
                {
                    "upn": "foreign@foreign-graph.test",
                    "display_name": "Foreign",
                    "password": secrets.token_urlsafe(24),
                },
            )
            app = post(
                base + "/applications",
                {
                    "display_name": "Graph SDK application",
                    "client_mode": "confidential",
                    "required_permissions": [
                        {"resource": "graph", "kind": "application", "permissions": PERMISSIONS},
                        {
                            "resource": "exchange",
                            "kind": "application",
                            "permissions": ["Exchange.ManageAsApp"],
                        },
                    ],
                },
            )
            secret = post(
                base + f"/applications/{app['client_id']}/credentials", {"kind": "secret"}
            )
            for resource, permissions in (
                ("graph", PERMISSIONS),
                ("exchange", ["Exchange.ManageAsApp"]),
            ):
                put(
                    base + f"/grants/{uuid4()}",
                    {
                        "client_id": app["client_id"],
                        "resource": resource,
                        "kind": "application",
                        "permissions": permissions,
                    },
                )
            public = post(
                base + "/applications",
                {
                    "display_name": "Graph SDK delegated",
                    "client_mode": "public",
                    "redirect_uris": ["http://localhost:8123/callback"],
                    "required_permissions": [
                        {
                            "resource": "graph",
                            "kind": "delegated",
                            "permissions": ["User.Read", *PERMISSIONS],
                        }
                    ],
                },
            )
            put(
                base + f"/grants/{uuid4()}",
                {
                    "client_id": public["client_id"],
                    "resource": "graph",
                    "kind": "delegated",
                    "user_id": user["id"],
                    "permissions": ["User.Read", *PERMISSIONS],
                },
            )
            negative = post(
                base + "/applications",
                {
                    "display_name": "Graph SDK denied",
                    "client_mode": "confidential",
                    "required_permissions": [
                        {
                            "resource": "graph",
                            "kind": "application",
                            "permissions": ["Organization.Read.All"],
                        }
                    ],
                },
            )
            negative_secret = post(
                base + f"/applications/{negative['client_id']}/credentials", {"kind": "secret"}
            )
            put(
                base + f"/grants/{uuid4()}",
                {
                    "client_id": negative["client_id"],
                    "resource": "graph",
                    "kind": "application",
                    "permissions": ["Organization.Read.All"],
                },
            )
            result = post(f"/_mock/tenants/{tenant['id']}/directory/import", seed)
            if result["objects"] != 5000:
                raise RuntimeError("M3B seed import did not create 5,000 users")
            state = {
                "tenant_id": tenant["id"],
                "client_id": app["client_id"],
                "public_client_id": public["client_id"],
                "user_id": user["id"],
                "negative_client_id": negative["client_id"],
                "foreign_tenant_id": foreign["id"],
                "foreign_user_id": foreign_user["id"],
                "secret": secret["secret"],
                "negative_secret": negative_secret["secret"],
                "user": {"username": user["upn"], "password": user_password},
            }
            save(record, state)
        public_state = {
            key: value
            for key, value in state.items()
            if key not in {"secret", "negative_secret", "user"}
        }
        ordered = sorted(seed["users"], key=lambda row: row["id"])
        public_state.update(
            seed_ids=[row["id"] for row in ordered],
            seed_first_id=ordered[0]["id"],
            seed_first_display_name=ordered[0]["displayName"],
            seed_user_count=5000,
            domain="graph.test",
            delegated_scopes=["User.Read", *PERMISSIONS],
        )
        for language in ("python", "powershell", "native"):
            directory = RUNTIME / "clients" / f"{language}-state"
            native = language == "native"
            prefix = directory.as_posix() if native else "/state"
            config = {
                "schema_version": 1,
                **public_state,
                "endpoints": {name: PROFILE.text("https://localhost:51444") for name in CONTAINER}
                if native
                else CONTAINER,
                "ca_file": (INSTANCE / "ca.pem").as_posix() if native else "/certs/ca.pem",
                "secret_file": prefix + "/secret.json",
                "user_file": prefix + "/user.json",
                "negative_secret_file": prefix + "/negative-secret.json",
                "output_file": prefix + "/graph-evidence.json",
                "token_output_dir": prefix,
            }
            save(directory / "client.json", config)
            save(directory / "secret.json", {"secret": state["secret"]})
            save(directory / "negative-secret.json", {"secret": state["negative_secret"]})
            save(directory / "user.json", state["user"])
    print("M3B synthetic users, applications, grants, and 5,000-user fixture are ready")


def evidence_records(state):
    """Deterministic public records; no authentication or tenant credentials."""
    from datetime import UTC, datetime, timedelta

    when = datetime(2026, 10, 7, 12, tzinfo=UTC)
    user = state["user_id"]
    values = []
    for i in range(10000):
        family = "signIn" if i < 6000 else "directoryAudit"
        stamp = (when + timedelta(seconds=i)).isoformat()
        identifier = str(uuid5(NAMESPACE, "evidence-" + str(i)))
        if family == "signIn":
            payload = {
                "id": identifier,
                "createdDateTime": stamp,
                "userId": user,
                "userDisplayName": "SDK Admin",
                "userPrincipalName": "sdk-admin@graph.test",
                "appId": state["client_id"],
                "appDisplayName": "Graph SDK application",
                "ipAddress": "192.0.2.10",
                "isInteractive": True,
                "status": {"errorCode": 0, "failureReason": None, "additionalDetails": None},
                "conditionalAccessStatus": "notApplied",
                "riskDetail": "none",
                "riskLevelAggregated": "none",
                "riskLevelDuringSignIn": "none",
                "riskState": "none",
                "riskEventTypes_v2": [],
            }
        else:
            payload = {
                "id": identifier,
                "activityDateTime": stamp,
                "category": "UserManagement",
                "activityDisplayName": "Update user",
                "loggedByService": "Core Directory",
                "operationType": "Update",
                "result": "success",
                "resultReason": None,
                "correlationId": str(uuid5(NAMESPACE, "correlation-" + str(i))),
                "initiatedBy": {
                    "user": {
                        "id": user,
                        "displayName": "SDK Admin",
                        "userPrincipalName": "sdk-admin@graph.test",
                    }
                },
                "targetResources": [
                    {
                        "id": user,
                        "type": "User",
                        "displayName": "SDK Admin",
                        "userPrincipalName": "sdk-admin@graph.test",
                        "modifiedProperties": [],
                    }
                ],
                "additionalDetails": [],
            }
        values.append(
            {
                "family": family,
                "api_version": "v1.0",
                "payload": payload,
                "occurred_at": stamp,
                "available_at": stamp,
                "origin": "historical",
                "entitlement_snapshot": "P2",
            }
        )
    return values


def provision_evidence():
    from datetime import UTC, datetime

    state = json.loads((RUNTIME / "provisioning.json").read_text())
    base = f"/_mock/tenants/{state['tenant_id']}"
    with httpx.Client(
        base_url=HOST["graph"],
        verify=ssl.create_default_context(cafile=str(INSTANCE / "ca.pem")),
        trust_env=False,
        timeout=180,
        headers={"Authorization": "Bearer " + (INSTANCE / "admin.token").read_text().strip()},
    ) as client:
        checked(
            client.patch(
                base + "/evidence/settings",
                json={"delay_seconds": 0, "retention_days": 3650, "clock_mode": "wall"},
            )
        )
        sku = "84a661c4-e949-4bd2-a560-ed7766fcaf2b"
        checked(
            client.put(
                base + "/licensing/skus/" + sku,
                json={
                    "sku_part_number": "AAD_PREMIUM_P2",
                    "status": "Enabled",
                    "enabled": 6000,
                    "suspended": 0,
                    "warning": 0,
                    "allowed_usage_locations": [],
                    "service_plans": [
                        {
                            "servicePlanId": "eec0eb4f-6444-4f95-aba0-50c24d67f998",
                            "servicePlanName": "AAD_PREMIUM_P2",
                            "provisioningStatus": "Success",
                            "appliesTo": "User",
                        }
                    ],
                },
            )
        )
        token = checked(
            client.post(
                f"/{state['tenant_id']}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": state["client_id"],
                    "client_secret": state["secret"],
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
        )["access_token"]
        auth = {"Authorization": "Bearer " + token}
        role_path = base + "/identity/users/" + state["user_id"] + "/roles/Global Administrator"
        checked(client.delete(role_path))
        try:
            checked(
                client.patch(
                    "/v1.0/users/" + state["user_id"], headers=auth, json={"usageLocation": "US"}
                ),
                204,
            )
            checked(
                client.post(
                    "/v1.0/users/" + state["user_id"] + "/assignLicense",
                    headers=auth,
                    json={
                        "addLicenses": [{"skuId": sku, "disabledPlans": []}],
                        "removeLicenses": [],
                    },
                )
            )
        finally:
            checked(client.put(role_path, json={}))
        records = evidence_records(state)
        for start in range(0, len(records), 500):
            result = checked(
                client.post(
                    base + "/evidence/import",
                    json={
                        "schema_version": 1,
                        "provenance": {
                            "source": "MockAzure M3B qualification",
                            "seed": 42,
                            "generator": "mockazure-evidence-v1",
                        },
                        "records": records[start : start + 500],
                    },
                )
            )
            assert result["inserted"] + result["unchanged"] == min(500, len(records) - start)
        # Request IDs and occurrence timestamps are stable across preparation replays.
        checked(
            client.post(
                base + "/evidence/risk-observations",
                json={
                    "user_id": state["user_id"],
                    "event_type": "unfamiliarFeatures",
                    "level": "high",
                    "occurred_at": "2026-10-07T16:00:00Z",
                    "request_id": str(uuid5(NAMESPACE, "risk-observation")),
                },
            )
        )
        managed_path = RUNTIME / "managed-provisioning.json"
        if managed_path.exists():
            managed = json.loads(managed_path.read_text())
        else:
            identity = checked(
                client.post(
                    base + "/managed-identities",
                    json={
                        "display_name": "Evidence managed identity",
                        "kind": "system",
                        "resource_id": "/subscriptions/00000000-0000-0000-0000-000000000001/"
                        "resourceGroups/evidence/providers/Microsoft.Web/sites/collector",
                    },
                ),
                201,
            )
            binding = checked(
                client.post(
                    base + "/managed-identity-hosts",
                    json={
                        "name": "Evidence SDK host",
                        "system_identity_id": identity["id"],
                        "user_identity_ids": [],
                    },
                ),
                201,
            )
            checked(
                client.put(
                    base + "/identity/grants/" + str(uuid4()),
                    json={
                        "client_id": identity["client_id"],
                        "resource": "graph",
                        "kind": "application",
                        "permissions": ["Organization.Read.All", "AuditLog.Read.All"],
                    },
                )
            )
            managed = {"identity": identity, "binding": binding}
            save(managed_path, managed)
        for language in ("native", "python", "powershell"):
            directory = RUNTIME / "clients" / (language + "-state")
            config = json.loads((directory / "client.json").read_text())
            native = language == "native"
            prefix = directory.as_posix() if native else "/state"
            header = directory / "managed.header"
            if not header.exists():
                write_private(header, (managed["binding"]["header"] + "\n").encode())
            config.update(
                sku_id=sku,
                evidence_seed_count=len(records),
                evidence_ids={
                    family: sorted(r["payload"]["id"] for r in records if r["family"] == family)
                    for family in ("signIn", "directoryAudit")
                },
                managed_identity_client_id=managed["identity"]["client_id"],
                managed_identity_endpoint=PROFILE.text("http://localhost:51446/MSI/token")
                if native
                else "http://identity.mockazure.test:8080/MSI/token",
                managed_identity_header_file=prefix + "/managed.header",
            )
            save(directory / "client.json", config)
        save(
            RUNTIME / "evidence-seed-public.json",
            {
                "prepared_at": datetime.now(UTC).isoformat(),
                "ids": {
                    family: sorted(r["payload"]["id"] for r in records if r["family"] == family)
                    for family in ("signIn", "directoryAudit")
                },
            },
        )
    print("M3B evidence, inventory and managed identity provisioning complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provision", action="store_true")
    parser.add_argument("--config-output", type=Path, default=RUNTIME / "instance/native.json")
    args = parser.parse_args()
    if args.config_output.resolve() != (INSTANCE / "native.json").resolve():
        parser.error(
            PROFILE.text(
                "--config-output must name this checkout's .runtime/m3b/instance/native.json"
            )
        )
    initialize()
    if args.provision:
        provision()
        provision_evidence()
