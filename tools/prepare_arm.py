"""Prepare only this checkout's isolated M5 runtime and synthetic directory."""

import argparse
import json
import secrets
import ssl
from pathlib import Path
from uuid import UUID, uuid5

import httpx

from mockazure.bootstrap import initialize_instance
from mockazure.files import write_private
from mockazure.tls import renew_leaf

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime/m5"
INSTANCE = RUNTIME / "instance"
NAMESPACE = UUID("1bcfc9ba-f878-4396-b1b8-7402c4e357da")
CONTAINER = {
    "login": "https://login.mockazure.test",
    "graph": "https://graph.mockazure.test",
    "exchange": "https://outlook.mockazure.test",
    "arm": "https://management.mockazure.test",
    "browser_login": "https://localhost:36443",
}
HOST = {name: "https://localhost:36443" for name in CONTAINER}


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

        for port in (36443, 55444, 36444, 36445, 36446):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
    RUNTIME.mkdir(parents=True, exist_ok=True)
    secret = RUNTIME / "db.password"
    if not secret.exists():
        write_private(secret, (secrets.token_urlsafe(32) + "\n").encode())
    password = secret.read_text(encoding="utf-8").strip()
    files = initialize_instance(
        INSTANCE,
        database_url=f"postgresql://mockazure_m5:{password}@127.0.0.1:55444/mockazure_m5",
        advertised_host="localhost",
        port=36443,
    )
    config = json.loads(files.config_file.read_text(encoding="utf-8"))
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
        managed_identity_listen_port=36446,
    )
    save(files.config_file, config)
    config.update(
        listen_host="0.0.0.0",
        listen_port=8443,
        managed_identity_listen_host="0.0.0.0",
        managed_identity_listen_port=8080,
        database_url=f"postgresql+psycopg://mockazure_m5:{password}@db:5432/mockazure_m5",
    )
    if not (INSTANCE / "container.json").exists():
        renew_leaf(INSTANCE, aliases)
    save(INSTANCE / "container.json", config)
    for language in ("python", "powershell", "native"):
        for folder in ("state", "root", "tmp"):
            (RUNTIME / "clients" / f"{language}-{folder}").mkdir(parents=True, exist_ok=True)
    (RUNTIME / "api-state").mkdir(exist_ok=True)
    print("M5 configuration ready under .runtime/m5; credentials retained locally")


def checked(response, expected=200):
    if response.status_code != expected:
        raise RuntimeError(
            f"M5 preparation request failed ({response.status_code}, expected {expected})"
        )
    return response.json() if response.content else None


def provision():
    """Journal every local create; only this runtime's credentials are persisted."""
    with httpx.Client(
        base_url=HOST["arm"],
        trust_env=False,
        timeout=120,
        verify=ssl.create_default_context(cafile=str(INSTANCE / "ca.pem")),
        headers={
            "Authorization": "Bearer "
            + (INSTANCE / "admin.token").read_text(encoding="utf-8").strip()
        },
    ) as client:
        record = RUNTIME / "provisioning.json"
        state = json.loads(record.read_text(encoding="utf-8")) if record.exists() else {}
        journal_path = RUNTIME / "provisioning-journal.json"
        journal = (
            json.loads(journal_path.read_text(encoding="utf-8")) if journal_path.exists() else {}
        )
        if "password" not in journal:
            journal["password"] = secrets.token_urlsafe(24)
            save(journal_path, journal)

        def post(key, path, body):
            if key not in journal:
                journal[key] = checked(client.post(path, json=body), 201)
                save(journal_path, journal)
            return journal[key]

        tenant = post(
            "tenant",
            "/_mock/tenants",
            {
                "slug": "arm-lab",
                "display_name": "ARM Qualification",
                "domain": "arm.test",
                "licences": ["E5"],
            },
        )
        foreign = post(
            "foreign",
            "/_mock/tenants",
            {
                "slug": "arm-foreign",
                "display_name": "Foreign ARM",
                "domain": "foreign-arm.test",
                "licences": [],
            },
        )
        base = f"/_mock/tenants/{tenant['id']}"
        identity = base + "/identity"
        user = post(
            "user",
            identity + "/users",
            {"upn": "owner@arm.test", "display_name": "ARM Owner", "password": journal["password"]},
        )
        negative_user = post(
            "negative_user",
            identity + "/users",
            {
                "upn": "denied@arm.test",
                "display_name": "ARM Denied",
                "password": journal["password"],
            },
        )
        app = post(
            "app",
            identity + "/applications",
            {
                "display_name": "ARM client",
                "client_mode": "confidential",
                "required_permissions": [
                    {
                        "resource": "graph",
                        "kind": "application",
                        "permissions": ["Application.Read.All"],
                    }
                ],
            },
        )
        secret = post(
            "secret", identity + f"/applications/{app['client_id']}/credentials", {"kind": "secret"}
        )
        public = post(
            "public",
            identity + "/applications",
            {
                "display_name": "ARM delegated",
                "client_mode": "public",
                "redirect_uris": ["http://localhost:8123/callback"],
                "required_permissions": [
                    {"resource": "arm", "kind": "delegated", "permissions": ["user_impersonation"]}
                ],
            },
        )
        negative = post(
            "negative",
            identity + "/applications",
            {"display_name": "ARM denied", "client_mode": "confidential"},
        )
        negative_secret = post(
            "negative_secret",
            identity + f"/applications/{negative['client_id']}/credentials",
            {"kind": "secret"},
        )
        checked(
            client.put(
                identity + f"/grants/{uuid5(NAMESPACE, 'graph-bootstrap')}",
                json={
                    "client_id": app["client_id"],
                    "resource": "graph",
                    "kind": "application",
                    "permissions": ["Application.Read.All"],
                },
            )
        )
        for person in (user, negative_user):
            checked(
                client.put(
                    identity + f"/grants/{uuid5(NAMESPACE, person['id'])}",
                    json={
                        "client_id": public["client_id"],
                        "resource": "arm",
                        "kind": "delegated",
                        "user_id": person["id"],
                        "permissions": ["user_impersonation"],
                    },
                )
            )
        token = checked(
            client.post(
                f"/{tenant['id']}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": app["client_id"],
                    "client_secret": secret["secret"],
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
        )["access_token"]
        principals = checked(
            client.get(
                "/v1.0/servicePrincipals",
                params={"$filter": f"appId eq '{app['client_id']}'"},
                headers={"Authorization": "Bearer " + token},
            )
        )["value"]
        assert len(principals) == 1
        sub = post("subscription", base + "/subscriptions", {"display_name": "ARM evidence lab"})
        foreign_sub = post(
            "foreign_subscription",
            f"/_mock/tenants/{foreign['id']}/subscriptions",
            {"display_name": "Foreign ARM evidence"},
        )
        subscription_id = sub["id"]
        scope = f"/subscriptions/{subscription_id}"
        admin = base + "/subscriptions/" + subscription_id
        assignments = {}
        for label, principal_id, kind in (
            ("application", principals[0]["id"], "ServicePrincipal"),
            ("delegated", user["id"], "User"),
        ):
            identifier = str(uuid5(NAMESPACE, "owner-" + label))
            checked(
                client.put(
                    admin + "/role-assignments/" + identifier,
                    json={
                        "principal_id": principal_id,
                        "principal_type": kind,
                        "scope": scope,
                        "role": "Owner",
                    },
                )
            )
            assignments[label] = identifier
        checked(
            client.put(
                admin + "/resource-groups/evidence",
                json={"location": "eastus", "tags": {"Purpose": "Evidence"}},
            )
        )
        resource_scope = (
            scope + "/resourceGroups/evidence/providers/Microsoft.Compute/virtualMachines"
        )
        resources = [
            {
                "id": resource_scope + f"/vm{i:04d}",
                "name": f"vm{i:04d}",
                "type": "Microsoft.Compute/virtualMachines",
                "location": "eastus",
                "tags": {"Purpose": "Evidence"},
                "properties": {"hardwareProfile": {"vmSize": "Standard_D2s_v5"}},
            }
            for i in range(103)
        ]
        checked(client.post(admin + "/resources", json={"resources": resources}))
        state.update(
            tenant_id=tenant["id"],
            foreign_tenant_id=foreign["id"],
            subscription_id=subscription_id,
            foreign_subscription_id=foreign_sub["id"],
            client_id=app["client_id"],
            service_principal_id=principals[0]["id"],
            public_client_id=public["client_id"],
            negative_client_id=negative["client_id"],
            user_id=user["id"],
            negative_user_id=negative_user["id"],
            assignments=assignments,
            secret=secret["secret"],
            negative_secret=negative_secret["secret"],
            user={"username": user["upn"], "password": journal["password"]},
            negative_user={"username": negative_user["upn"], "password": journal["password"]},
            resource_ids=[r["id"] for r in resources],
        )
        save(record, state)
        public_state = {
            k: v
            for k, v in state.items()
            if k not in {"secret", "negative_secret", "user", "negative_user"}
        }
        for language in ("python", "powershell", "native"):
            directory = RUNTIME / "clients" / f"{language}-state"
            native = language == "native"
            prefix = directory.as_posix() if native else "/state"
            config = {
                "schema_version": 1,
                **public_state,
                "endpoints": {name: "https://localhost:36444" for name in CONTAINER}
                if native
                else CONTAINER,
                "ca_file": (INSTANCE / "ca.pem").as_posix() if native else "/certs/ca.pem",
                "secret_file": prefix + "/secret.json",
                "user_file": prefix + "/user.json",
                "negative_secret_file": prefix + "/negative-secret.json",
                "negative_user_file": prefix + "/negative-user.json",
                "output_file": prefix + "/arm.json",
                "token_output_dir": prefix,
            }
            save(directory / "client.json", config)
            for name, value in (
                ("secret", {"secret": state["secret"]}),
                ("negative-secret", {"secret": state["negative_secret"]}),
                ("user", state["user"]),
                ("negative-user", state["negative_user"]),
            ):
                save(directory / (name + ".json"), value)
    print("M5 local ARM identities, subscriptions, Owner grants and103 resources provisioned")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["initialize", "provision"])
    args = parser.parse_args()
    initialize() if args.action == "initialize" else provision()
