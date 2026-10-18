"""Prepare only this checkout's isolated M3C runtime and synthetic directory."""

import argparse
import hashlib
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

PROFILE = profile_for("m3c")
RUNTIME = ROOT / PROFILE.text(".runtime/m3c")
INSTANCE = RUNTIME / "instance"
NAMESPACE = UUID("1bcfc9ba-f878-4396-b1b8-7402c4e357da")
PERMISSIONS = [
    "Application.ReadWrite.All",
    "AppRoleAssignment.ReadWrite.All",
    "DelegatedPermissionGrant.ReadWrite.All",
    "RoleManagement.ReadWrite.Directory",
    "Device.ReadWrite.All",
    "UserAuthenticationMethod.Read.All",
    "Policy.Read.All",
    "Policy.ReadWrite.ConditionalAccess",
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
DELEGATED_PERMISSIONS = [
    "User.Read",
    "Directory.AccessAsUser.All",
    *(name for name in PERMISSIONS if name != "Device.ReadWrite.All"),
]
CONTAINER = {
    "login": "https://login.mockazure.test",
    "graph": "https://graph.mockazure.test",
    "exchange": "https://outlook.mockazure.test",
    "arm": "https://management.mockazure.test",
    "browser_login": PROFILE.text("https://localhost:32443"),
}
HOST = {name: PROFILE.text("https://localhost:32443") for name in CONTAINER}


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
            PROFILE.port(32443),
            PROFILE.port(55440),
            PROFILE.port(32444),
            PROFILE.port(32445),
            PROFILE.port(32446),
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
            f"postgresql://mockazure_m3c:{password}@127.0.0.1:55440/mockazure_m3c"
        ),
        advertised_host="localhost",
        port=PROFILE.port(32443),
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
        managed_identity_listen_port=PROFILE.port(32446),
    )
    save(files.config_file, config)
    config.update(
        listen_host="0.0.0.0",
        listen_port=8443,
        managed_identity_listen_host="0.0.0.0",
        managed_identity_listen_port=8080,
        database_url=PROFILE.text(
            f"postgresql+psycopg://mockazure_m3c:{password}@db:5432/mockazure_m3c"
        ),
    )
    if not (INSTANCE / "container.json").exists():
        renew_leaf(INSTANCE, aliases)
    save(INSTANCE / "container.json", config)
    for language in ("python", "powershell", "native"):
        for folder in ("state", "root", "tmp"):
            (RUNTIME / "clients" / f"{language}-{folder}").mkdir(parents=True, exist_ok=True)
    (RUNTIME / "api-state").mkdir(exist_ok=True)
    print(PROFILE.text("M3C configuration ready under .runtime/m3c; credentials retained locally"))


def checked(response, expected=200):
    if response.status_code != expected:
        raise RuntimeError(
            f"M3C preparation request failed ({response.status_code}, expected {expected})"
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
        "provenance": {"source": "MockAzure M3C qualification", "seed": 1},
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
            journal_path = RUNTIME / "provisioning-journal.json"
            journal = json.loads(journal_path.read_text()) if journal_path.exists() else {}
            if "user_password" not in journal:
                journal["user_password"] = secrets.token_urlsafe(24)
                save(journal_path, journal)

            def post(path, data):
                key = hashlib.sha256((path + json.dumps(data, sort_keys=True)).encode()).hexdigest()
                if key in journal:
                    return journal[key]
                # Recover this runtime's interrupted bootstrap without deleting state.
                existing = None
                if path == "/_mock/tenants":
                    rows = checked(client.get(path))["items"]
                    existing = next((row for row in rows if row["slug"] == data["slug"]), None)
                    if existing is not None:
                        assert existing["domain"] == data["domain"]
                elif path.endswith("/applications"):
                    rows = checked(client.get(path))["items"]
                    existing = next(
                        (row for row in rows if row["display_name"] == data["display_name"]),
                        None,
                    )
                result = existing or checked(client.post(path, json=data), 201)
                journal[key] = result
                save(journal_path, journal)
                return result

            def put(path, data):
                if "/grants/" in path:
                    stable = uuid5(
                        NAMESPACE, path.rsplit("/", 1)[0] + json.dumps(data, sort_keys=True)
                    )
                    path = path.rsplit("/", 1)[0] + "/" + str(stable)
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
            user_password = journal["user_password"]
            user = post(
                base + "/users",
                {
                    "upn": "sdk-qualification@graph.test",
                    "display_name": "SDK Admin",
                    "password": user_password,
                },
            )
            put(base + f"/users/{user['id']}/roles/Global Administrator", {})
            foreign_user = post(
                f"/_mock/tenants/{foreign['id']}/identity/users",
                {
                    "upn": "qualification@foreign-graph.test",
                    "display_name": "Foreign",
                    "password": journal["user_password"],
                },
            )
            app = post(
                base + "/applications",
                {
                    "display_name": "Graph Security SDK application",
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
                            "permissions": DELEGATED_PERMISSIONS,
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
                    "permissions": DELEGATED_PERMISSIONS,
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
                raise RuntimeError("M3C seed import did not create 5,000 users")
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
        from tools.qualification_mfa import ensure_mobile_method

        ensure_mobile_method(client, state["tenant_id"], state["user_id"])
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
            delegated_scopes=DELEGATED_PERMISSIONS,
        )
        for language in ("python", "powershell", "native"):
            directory = RUNTIME / "clients" / f"{language}-state"
            native = language == "native"
            prefix = directory.as_posix() if native else "/state"
            config = {
                "schema_version": 1,
                **public_state,
                "endpoints": {name: PROFILE.text("https://localhost:32444") for name in CONTAINER}
                if native
                else CONTAINER,
                "ca_file": (INSTANCE / "ca.pem").as_posix() if native else "/certs/ca.pem",
                "secret_file": prefix + "/secret.json",
                "user_file": prefix + "/user.json",
                "negative_secret_file": prefix + "/negative-secret.json",
                "output_file": prefix + "/graph-security.json",
                "token_output_dir": prefix,
            }
            save(directory / "client.json", config)
            save(directory / "secret.json", {"secret": state["secret"]})
            save(directory / "negative-secret.json", {"secret": state["negative_secret"]})
            save(directory / "user.json", state["user"])
    print("M3C synthetic users, applications, grants, and 5,000-user fixture are ready")


def provision_security():
    state = json.loads((RUNTIME / "provisioning.json").read_text())
    base = f"/_mock/tenants/{state['tenant_id']}"
    with httpx.Client(
        base_url=HOST["graph"],
        verify=ssl.create_default_context(cafile=str(INSTANCE / "ca.pem")),
        trust_env=False,
        timeout=120,
        headers={"Authorization": "Bearer " + (INSTANCE / "admin.token").read_text().strip()},
    ) as client:
        checked(
            client.patch(
                base + "/evidence/settings",
                json={
                    "delay_seconds": 0,
                    "retention_days": 3650,
                    "clock_mode": "wall",
                },
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
        method_path = base + "/security/users/" + state["user_id"] + "/authenticationMethods"
        existing = checked(client.get(method_path))
        values = existing["value"] if "value" in existing else existing.get("items", [])
        enrolled = {value["@odata.type"] for value in values}
        for descriptor in (
            {
                "@odata.type": "#microsoft.graph.phoneAuthenticationMethod",
                "phoneType": "mobile",
                "phoneNumber": "+1 5555550150",
            },
            {
                "@odata.type": "#microsoft.graph.emailAuthenticationMethod",
                "emailAddress": "recovery@graph.test",
            },
            {
                "@odata.type": "#microsoft.graph.microsoftAuthenticatorAuthenticationMethod",
                "displayName": "Qualification handset",
            },
        ):
            if descriptor["@odata.type"] not in enrolled:
                checked(client.post(method_path, json=descriptor), 201)
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
        apps = checked(
            client.get(
                "/v1.0/applications",
                headers=auth,
                params={"$filter": "appId eq '" + state["client_id"] + "'"},
            )
        )["value"]
        sps = checked(
            client.get(
                "/v1.0/servicePrincipals",
                headers=auth,
                params={"$filter": "appId eq '" + state["client_id"] + "'"},
            )
        )["value"]
        assert len(apps) == len(sps) == 1
        record = RUNTIME / "security-provisioning.json"
        if not record.exists():
            key = checked(
                client.post(
                    "/v1.0/servicePrincipals/" + sps[0]["id"] + "/addPassword",
                    headers=auth,
                    json={
                        "passwordCredential": {
                            "displayName": "Collector service principal credential",
                        }
                    },
                ),
                200,
            )
            policy = checked(
                client.post(
                    "/v1.0/identity/conditionalAccess/policies",
                    headers=auth,
                    json={
                        "displayName": "Qualification collector policy",
                        "state": "disabled",
                        "conditions": {
                            "users": {"includeUsers": ["All"]},
                            "applications": {"includeApplications": ["All"]},
                            "clientAppTypes": ["all"],
                        },
                        "grantControls": {"operator": "OR", "builtInControls": ["mfa"]},
                    },
                ),
                201,
            )
            save(record, {"service_principal_key_id": key["keyId"], "policy_id": policy["id"]})
        security = json.loads(record.read_text())
        security.update(application_object_id=apps[0]["id"], service_principal_id=sps[0]["id"])
        for language in ("python", "powershell", "native"):
            path = RUNTIME / "clients" / (language + "-state") / "client.json"
            values = json.loads(path.read_text())
            values.update(security)
            save(path, values)
    print("M3C licence, method inventory, credentials and disabled policy provisioned")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["initialize", "provision"])
    args = parser.parse_args()
    if args.action == "initialize":
        initialize()
    else:
        provision()
        provision_security()
