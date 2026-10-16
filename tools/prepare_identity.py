"""Prepare the dedicated M2 lab, then provision its synthetic connection identities."""

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def initialize():
    import json
    import secrets
    from pathlib import Path

    from mockazure.bootstrap import initialize_instance
    from mockazure.files import write_private
    from mockazure.tls import renew_leaf

    root = Path(".runtime/m2")
    root.mkdir(parents=True, exist_ok=True)
    secret = root / "db.password"
    if not secret.exists():
        write_private(secret, (secrets.token_urlsafe(32) + "\n").encode())
    password = secret.read_text().strip()
    instance = root / "instance"
    files = initialize_instance(
        instance,
        database_url=f"postgresql://mockazure_m2:{password}@127.0.0.1:55434/mockazure_m2",
        advertised_host="localhost",
        port=38443,
    )
    container = instance / "container.json"
    if not container.exists():
        data = json.loads(files.config_file.read_text())
        aliases = [
            "login.mockazure.test",
            "graph.mockazure.test",
            "outlook.mockazure.test",
            "management.mockazure.test",
        ]
        data.update(
            listen_host="0.0.0.0",
            listen_port=8443,
            database_url=f"postgresql://mockazure_m2:{password}@db:5432/mockazure_m2",
            allowed_hosts=["localhost", "127.0.0.1", *aliases],
            service_endpoints={
                "login": "https://login.mockazure.test",
                "graph": "https://graph.mockazure.test",
                "exchange": "https://outlook.mockazure.test",
                "arm": "https://management.mockazure.test",
                "browser_login": "https://localhost:38443",
            },
        )
        renew_leaf(instance, data["allowed_hosts"])
        write_private(container, (json.dumps(data, indent=2) + "\n").encode())
    print("M2 instance configuration ready; credentials retained locally")

    import json
    from pathlib import Path

    root = Path(".runtime/m2/instance")
    container = json.loads((root / "container.json").read_text())
    host = {name: "https://localhost:38443" for name in container["service_endpoints"]}
    profiles = {"host": host, "container": container["service_endpoints"]}
    for name in ("container.json", "native.json"):
        path = root / name
        data = json.loads(path.read_text())
        data["client_profiles"] = profiles
        data["service_endpoints"] = container["service_endpoints"]
        path.write_text(json.dumps(data, indent=2))


def provision():
    if not Path(".runtime/clients/powershell-state/client.json").exists():
        provision_application()
    provision_connections()


def provision_application():
    import json
    import pathlib
    import ssl
    from uuid import uuid4

    import httpx

    from mockazure.files import write_private

    root = pathlib.Path(".runtime/m2")
    instance = root / "instance"
    with httpx.Client(
        base_url="https://localhost:38443",
        verify=ssl.create_default_context(cafile=str(instance / "ca.pem")),
        trust_env=False,
        headers={"Authorization": "Bearer " + (instance / "admin.token").read_text().strip()},
    ) as c:
        tenants = c.get("/_mock/tenants").json()["items"]
        tenant = next((t for t in tenants if t["slug"] == "identity-lab"), None)
        if tenant is None:
            r = c.post(
                "/_mock/tenants",
                json={
                    "slug": "identity-lab",
                    "domain": "identity.test",
                    "display_name": "Identity Lab",
                    "licences": ["E5"],
                },
            )
            r.raise_for_status()
            tenant = r.json()
        base = f"/_mock/tenants/{tenant['id']}/identity"
        permissions = [
            {"resource": "graph", "kind": "application", "permissions": ["Organization.Read.All"]},
            {
                "resource": "exchange",
                "kind": "application",
                "permissions": ["Exchange.ManageAsApp"],
            },
        ]
        r = c.post(
            base + "/applications",
            json={
                "display_name": "SDK probe",
                "client_mode": "confidential",
                "required_permissions": permissions,
            },
        )
        r.raise_for_status()
        app = r.json()
        r = c.post(base + f"/applications/{app['client_id']}/credentials", json={"kind": "secret"})
        r.raise_for_status()
        secret = r.json()
        for grant in permissions:
            r = c.put(base + f"/grants/{uuid4()}", json={"client_id": app["client_id"], **grant})
            r.raise_for_status()
        config = {
            "schema_version": 1,
            "tenant_id": tenant["id"],
            "client_id": app["client_id"],
            "tenant_domain": tenant["domain"],
            "endpoints": {
                "login": "https://login.mockazure.test",
                "graph": "https://graph.mockazure.test",
                "exchange": "https://outlook.mockazure.test",
                "arm": "https://management.mockazure.test",
            },
            "ca_file": "/certs/ca.pem",
            "secret_file": "/state/secret.json",
        }
        for name in ("powershell-state", "python-state"):
            state = pathlib.Path(".runtime/clients") / name
            state.mkdir(parents=True, exist_ok=True)
            (state / "client.json").write_text(json.dumps(config, indent=2))
            # This is an explicitly generated local lab credential, never a real tenant secret.
            target = state / "secret.json"
            if target.exists():
                target.unlink()
            write_private(target, json.dumps(secret).encode())
    print("SDK probe identities and local credential files prepared")


def provision_connections():
    import json
    import secrets
    import ssl
    from pathlib import Path
    from uuid import uuid4

    import httpx

    root = Path(".runtime/m2/instance")
    with httpx.Client(
        base_url="https://localhost:38443",
        verify=ssl.create_default_context(cafile=str(root / "ca.pem")),
        trust_env=False,
        headers={"Authorization": "Bearer " + (root / "admin.token").read_text().strip()},
    ) as client:
        state = Path(".runtime/clients/powershell-state")
        probe = json.loads((state / "client.json").read_text())
        tenant = probe["tenant_id"]
        base = f"/_mock/tenants/{tenant}/identity"

        def post(path, data):
            result = client.post(path, json=data)
            result.raise_for_status()
            return result.json()

        def put(path, data):
            result = client.put(path, json=data)
            result.raise_for_status()
            return result.json()

        put(base + f"/applications/{probe['client_id']}/roles/Global Reader", {})
        provisioned = state / "connection-provisioning.json"
        if provisioned.exists():
            extra = json.loads(provisioned.read_text())
        else:
            password = secrets.token_urlsafe(24)
            user = post(
                base + "/users",
                {
                    "upn": "sdk-reader@identity.test",
                    "display_name": "SDK Reader",
                    "password": password,
                },
            )
            public = post(
                base + "/applications",
                {
                    "display_name": "SDK delegated probes",
                    "client_mode": "public",
                    "redirect_uris": ["http://localhost:8123/callback"],
                    "required_permissions": [
                        {
                            "resource": "graph",
                            "kind": "delegated",
                            "permissions": ["User.Read", "Organization.Read.All"],
                        },
                        {
                            "resource": "exchange",
                            "kind": "delegated",
                            "permissions": ["Exchange.Manage"],
                        },
                        {
                            "resource": "arm",
                            "kind": "delegated",
                            "permissions": ["user_impersonation"],
                        },
                    ],
                },
            )
            for resource, scopes in (
                ("graph", ["User.Read", "Organization.Read.All"]),
                ("exchange", ["Exchange.Manage"]),
                ("arm", ["user_impersonation"]),
            ):
                put(
                    base + f"/grants/{uuid4()}",
                    {
                        "client_id": public["client_id"],
                        "resource": resource,
                        "kind": "delegated",
                        "permissions": scopes,
                        "user_id": user["id"],
                    },
                )
            put(base + f"/users/{user['id']}/roles/Global Reader", {})
            denied = post(
                base + "/applications",
                {
                    "display_name": "Denied SDK client",
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
                base + f"/applications/{denied['client_id']}/credentials", {"kind": "secret"}
            )
            subscription = post(
                f"/_mock/tenants/{tenant}/subscriptions", {"display_name": "SDK evidence lab"}
            )
            assignment = (
                f"/_mock/tenants/{tenant}/subscriptions/{subscription['id']}/assignments/{uuid4()}"
            )
            put(assignment, {"client_id": probe["client_id"]})
            put(
                f"/_mock/tenants/{tenant}/subscriptions/{subscription['id']}/assignments/{uuid4()}",
                {"user_id": user["id"]},
            )
            extra = {
                "public_client_id": public["client_id"],
                "user_id": user["id"],
                "denied_client_id": denied["client_id"],
                "subscription_id": subscription["id"],
                "user": {"username": user["upn"], "password": password},
                "negative_secret": negative_secret,
            }
            provisioned.write_text(json.dumps(extra))
        for name in ("powershell", "python"):
            directory = Path(f".runtime/clients/{name}-state")
            cfg = json.loads((directory / "client.json").read_text())
            cfg.update(
                {
                    k: extra[k]
                    for k in ("public_client_id", "user_id", "denied_client_id", "subscription_id")
                }
            )
            cfg.update(
                user_file="/state/user.json", negative_secret_file="/state/negative-secret.json"
            )
            (directory / "client.json").write_text(json.dumps(cfg, indent=2))
            (directory / "user.json").write_text(json.dumps(extra["user"]))
            (directory / "negative-secret.json").write_text(json.dumps(extra["negative_secret"]))
    print("Local SDK user, grants, negative client and subscription are provisioned.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provision",
        action="store_true",
        help="Provision identities over HTTPS after starting compose.m2.yaml",
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.provision:
        provision()
    else:
        initialize()
