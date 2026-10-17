"""Prepare only this checkout's isolated M4 runtime and synthetic directory."""

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

PROFILE = profile_for("m4")
RUNTIME = ROOT / PROFILE.text(".runtime/m4")
INSTANCE = RUNTIME / "instance"
NAMESPACE = UUID("1bcfc9ba-f878-4396-b1b8-7402c4e357da")
PERMISSIONS = [
    "MailboxSettings.ReadWrite",
    "AuditLogsQuery.Read.All",
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
    "browser_login": PROFILE.text("https://localhost:34443"),
}
HOST = {name: PROFILE.text("https://localhost:34443") for name in CONTAINER}


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
            PROFILE.port(34443),
            PROFILE.port(55442),
            PROFILE.port(34444),
            PROFILE.port(34445),
            PROFILE.port(34446),
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
            f"postgresql://mockazure_m4:{password}@127.0.0.1:55442/mockazure_m4"
        ),
        advertised_host="localhost",
        port=PROFILE.port(34443),
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
        managed_identity_listen_port=PROFILE.port(34446),
    )
    save(files.config_file, config)
    config.update(
        listen_host="0.0.0.0",
        listen_port=8443,
        managed_identity_listen_host="0.0.0.0",
        managed_identity_listen_port=8080,
        database_url=PROFILE.text(
            f"postgresql+psycopg://mockazure_m4:{password}@db:5432/mockazure_m4"
        ),
    )
    if not (INSTANCE / "container.json").exists():
        renew_leaf(INSTANCE, aliases)
    save(INSTANCE / "container.json", config)
    for language in ("python", "powershell", "native"):
        for folder in ("state", "root", "tmp"):
            (RUNTIME / "clients" / f"{language}-{folder}").mkdir(parents=True, exist_ok=True)
    (RUNTIME / "api-state").mkdir(exist_ok=True)
    print(PROFILE.text("M4 configuration ready under .runtime/m4; credentials retained locally"))


def checked(response, expected=200):
    if response.status_code != expected:
        raise RuntimeError(
            f"M4 preparation request failed ({response.status_code}, expected {expected})"
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
        "provenance": {"source": "MockAzure M4 qualification", "seed": 1},
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
                raise RuntimeError("M4 seed import did not create 5,000 users")
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
            delegated_scopes=DELEGATED_PERMISSIONS,
        )
        for language in ("python", "powershell", "native"):
            directory = RUNTIME / "clients" / f"{language}-state"
            native = language == "native"
            prefix = directory.as_posix() if native else "/state"
            config = {
                "schema_version": 1,
                **public_state,
                "endpoints": {name: PROFILE.text("https://localhost:34444") for name in CONTAINER}
                if native
                else CONTAINER,
                "ca_file": (INSTANCE / "ca.pem").as_posix() if native else "/certs/ca.pem",
                "secret_file": prefix + "/secret.json",
                "user_file": prefix + "/user.json",
                "negative_secret_file": prefix + "/negative-secret.json",
                "output_file": prefix + "/graph-mail.json",
                "token_output_dir": prefix,
            }
            save(directory / "client.json", config)
            save(directory / "secret.json", {"secret": state["secret"]})
            save(directory / "negative-secret.json", {"secret": state["negative_secret"]})
            save(directory / "user.json", state["user"])
    print("M4 synthetic users, applications, grants, and 5,000-user fixture are ready")


def provision_mail():
    state = json.loads((RUNTIME / "provisioning.json").read_text())
    with httpx.Client(
        base_url=PROFILE.text("https://localhost:34443"),
        verify=ssl.create_default_context(cafile=str(RUNTIME / "instance/ca.pem")),
        headers={
            "Authorization": "Bearer " + (RUNTIME / "instance/admin.token").read_text().strip()
        },
        trust_env=False,
        timeout=30,
    ) as client:
        base = f"/_mock/tenants/{state['tenant_id']}"
        for path, body in (
            (base + f"/identity/applications/{state['client_id']}/roles/Global Administrator", {}),
            (base + "/mail/settings", {"exchange_enabled": True, "audit_tier": "standard"}),
            (
                base + f"/mail/mailboxes/{state['user_id']}",
                {
                    "primary_smtp": state["user"]["username"],
                    "alias": "sdk-qualification",
                    "archive_enabled": True,
                    "folders": [
                        {
                            "id": "inbox",
                            "name": "Inbox",
                            "path": "/Inbox",
                            "item_count": 12,
                            "size_bytes": 4096,
                        },
                        {
                            "id": "review",
                            "name": "Review",
                            "path": "/Review",
                            "item_count": 2,
                            "size_bytes": 1024,
                        },
                        {
                            "id": "archive",
                            "name": "Archive",
                            "path": "/",
                            "archive": True,
                            "item_count": 3,
                            "size_bytes": 2048,
                        },
                    ],
                },
            ),
        ):
            r = client.put(path, json=body)
            if r.status_code != 200:
                raise RuntimeError(
                    f"Local setup failed: {r.status_code} at {path.rsplit('/', 1)[-1]}"
                )
    for lang in ("powershell", "python", "native"):
        path = RUNTIME / f"clients/{lang}-state/client.json"
        config = json.loads(path.read_text())
        config["tenant_domain"] = config["domain"]
        config["mailbox_upn"] = state["user"]["username"]
        path.write_text(json.dumps(config), encoding="utf-8")
    print("M4 local Exchange role, mailbox and persisted archive/folders configured")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["initialize", "provision"])
    args = parser.parse_args()
    if args.action == "initialize":
        initialize()
    else:
        provision()
        provision_mail()
