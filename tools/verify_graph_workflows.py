"""Four original Graph clients against isolated local API, worker and HTTPS receiver."""

import base64
import json
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from uuid import uuid4

import msal
from requests.exceptions import RequestException

from integrations.python.arm import LocalSession
from tools.verify_collections import PS_IMAGE
from tools.verify_default import ROOT

ECOSYSTEMS = ("python", "typescript", "dotnet", "powershell")


def callback_address(networks):
    addresses = [row["IPAddress"] for name, row in networks.items() if name.endswith("_internal")]
    if len(addresses) != 1:
        raise ValueError("The receiver requires one shared internal Docker network")
    address = ip_address(addresses[0])
    if (
        not address.is_private
        or address.is_loopback
        or address.is_unspecified
        or address.is_multicast
    ):
        raise ValueError("The receiver requires a private address shared by API and worker")
    return str(address)


def wait_for_fresh_connection(origin, ca):
    # A pooled connection can survive while ingress still rejects new sessions.
    # Probe the same verified, local-only transport used by MSAL after restarts.
    for _ in range(120):
        try:
            with LocalSession(origin, ca) as transport:
                response = transport.get(origin + "/_mock/ready", timeout=5, allow_redirects=False)
                if response.status_code == 200:
                    return
        except RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError("Local Graph restart readiness failed on fresh TLS connections")


@contextmanager
def local_receiver(directory, command, compose):
    state = directory / "graph-workflow-clients"
    state.mkdir(exist_ok=True)
    (state / "receiver-status.txt").write_text("202")
    instance = directory / ".runtime/instance"
    api = command(compose + ["ps", "-q", "api"], capture=True)
    image = command(["docker", "inspect", "--format", "{{.Config.Image}}", api], capture=True)
    networks = json.loads(
        command(
            ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", api],
            capture=True,
        )
    )
    callback_address(networks)
    network = next(name for name in networks if name.endswith("_internal"))
    receiver = "mockazure-graph-receiver-" + uuid4().hex[:12]
    command(
        [
            "docker",
            "run",
            "-d",
            "--name",
            receiver,
            "--network",
            network,
            "--entrypoint",
            "python",
            "--mount",
            f"type=bind,source={instance},target=/instance,readonly",
            "--mount",
            f"type=bind,source={ROOT / 'integrations/local_webhook_receiver.py'},"
            "target=/receiver.py,readonly",
            "--mount",
            f"type=bind,source={state},target=/state",
            image,
            "/receiver.py",
            "--host",
            "0.0.0.0",
            "--cert",
            "/instance/server.pem",
            "--key",
            "/instance/server.key",
            "--output",
            "/state/notifications.ndjson",
            "--status-file",
            "/state/receiver-status.txt",
        ]
    )
    try:
        networks = json.loads(
            command(
                ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", receiver],
                capture=True,
            )
        )
        yield callback_address(networks)
    finally:
        command(["docker", "rm", "-f", receiver])


def qualify(directory, command, compose, client, headers, tenant, check):
    with local_receiver(directory, command, compose) as receiver_ip:
        return _qualify(directory, command, compose, client, headers, tenant, check, receiver_ip)


def _qualify(directory, command, compose, client, headers, tenant, check, receiver_ip):
    state = directory / "graph-workflow-clients"
    instance = directory / ".runtime/instance"
    base = "/_mock/tenants/" + tenant["id"]
    ca = str(instance / "ca.pem")
    origin = str(client.base_url).rstrip("/")
    runtime = json.loads((instance / "container.json").read_text("utf-8"))
    callback = "https://localhost:9444/notifications"
    runtime["notification_receivers"] = {
        callback: {"address": receiver_ip, "ca_file": "/instance/ca.pem"}
    }
    (instance / "container.json").write_text(json.dumps(runtime, indent=2) + "\n", "utf-8")
    command(compose + ["restart", "api", "worker"])

    def ready():
        wait_for_fresh_connection(origin, ca)

    ready()
    api = command(compose + ["ps", "-q", "api"], capture=True)
    image = command(["docker", "inspect", "--format", "{{.Config.Image}}", api], capture=True)

    def request(method, path, value=None, status=200):
        response = client.request(method, path, headers=headers, json=value)
        assert response.status_code == status, (path, response.status_code)
        return response.json() if response.content else None

    app = request(
        "POST",
        base + "/identity/applications",
        {
            "display_name": "Graph native workflows",
            "client_mode": "confidential",
            "required_permissions": [
                {
                    "resource": "graph",
                    "kind": "application",
                    "permissions": ["Mail.ReadWrite", "Mail.Send", "Calendars.ReadWrite"],
                }
            ],
        },
        201,
    )
    appid = app["client_id"]
    secret = request(
        "POST", base + f"/identity/applications/{appid}/credentials", {"kind": "secret"}, 201
    )["secret"]
    request(
        "PUT",
        base + "/identity/grants/" + str(uuid4()),
        {
            "client_id": appid,
            "resource": "graph",
            "kind": "application",
            "permissions": ["Mail.ReadWrite", "Mail.Send", "Calendars.ReadWrite"],
        },
    )
    with LocalSession(origin, ca) as transport:
        auth = msal.ConfidentialClientApplication(
            appid,
            client_credential=secret,
            authority=origin + "/" + tenant["id"],
            validate_authority=False,
            instance_discovery=False,
            http_client=transport,
        )
        token = auth.acquire_token_for_client(["https://graph.microsoft.com/.default"])[
            "access_token"
        ]
    request("PUT", base + "/mail/settings", {"exchange_enabled": True, "audit_tier": "standard"})
    users = {}
    for name in (*ECOSYSTEMS, "recipient"):
        upn = name + "@" + tenant["domain"]
        user = request(
            "POST",
            base + "/identity/users",
            {"upn": upn, "display_name": name, "password": "Local-qualification-password!9"},
            201,
        )["id"]
        users[name] = user
        request("PUT", base + "/mail/mailboxes/" + user, {"primary_smtp": upn, "alias": name})
        request(
            "POST",
            base + "/mail/content/import",
            {
                "mailbox_user_id": user,
                "mode": "content-backed",
                "expected_revision": 0,
                "folders": [{"id": "inbox", "displayName": "Inbox", "well_known_name": "inbox"}],
                "messages": [],
            },
        )
    profiles = {}
    for ecosystem in ECOSYSTEMS:
        label = ecosystem + " Graph workflow"
        message = {
            "subject": label + " draft",
            "body": {"contentType": "text", "content": "Persistent SDK draft"},
        }
        sent = {
            "subject": label + " delivered",
            "toRecipients": [{"emailAddress": {"address": "recipient@" + tenant["domain"]}}],
            "attachments": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": "evidence.bin",
                    "contentBytes": base64.b64encode(b"local\0attachment").decode(),
                }
            ],
        }
        event = {
            "subject": label + " recurring",
            "start": {"dateTime": "2026-10-31T09:00:00", "timeZone": "Eastern Standard Time"},
            "end": {"dateTime": "2026-10-31T10:00:00", "timeZone": "Eastern Standard Time"},
            "recurrence": {
                "pattern": {"type": "daily", "interval": 1},
                "range": {
                    "type": "numbered",
                    "startDate": "2026-10-31",
                    "numberOfOccurrences": 3,
                    "recurrenceTimeZone": "Eastern Standard Time",
                },
            },
            "attendees": [
                {"type": "required", "emailAddress": {"address": "recipient@" + tenant["domain"]}}
            ],
        }
        profile = {
            "endpoint": origin,
            "ca_file": ca,
            "token": token,
            "owner": users[ecosystem],
            "recipient": users["recipient"],
            "label": label,
            "message_body": message,
            "send_body": sent,
            "event_body": event,
            "window_start": "2026-10-30T00:00:00Z",
            "window_end": "2026-11-04T00:00:00Z",
            "subscription_body": {
                "changeType": "created,updated,deleted",
                "resource": f"users/{users[ecosystem]}/messages",
                "notificationUrl": callback,
                "lifecycleNotificationUrl": callback,
                "expirationDateTime": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
                "clientState": ecosystem + "-local",
            },
            "fixtures": str(state / "fixtures.json"),
        }
        if ecosystem == "powershell":
            profile.update(
                endpoint="https://localhost:8443",
                ca_file="/certs/ca.pem",
                fixtures="/state/fixtures.json",
            )
        profiles[ecosystem] = profile
        (state / (ecosystem + ".json")).write_text(json.dumps(profile, indent=2) + "\n", "utf-8")

    def run(ecosystem, mode):
        profile = state / (ecosystem + ".json")
        report = state / (ecosystem + "-" + mode + ".json")
        if ecosystem == "python":
            args = [sys.executable, "-m", "integrations.python.graph_workflows"]
        elif ecosystem == "typescript":
            args = [
                sys.executable,
                "-m",
                "tools.run_cloud_client",
                "--ca",
                ca,
                "--",
                "node",
                str(ROOT / "work/cloud-e-clients/npm/runner.js"),
            ]
        elif ecosystem == "dotnet":
            args = ["dotnet", str(ROOT / "work/cloud-e-clients/dotnet/GraphWorkflows.dll")]
        else:
            args = [
                "docker",
                "run",
                "--rm",
                "--network",
                "container:" + api,
                "-e",
                "SSL_CERT_FILE=/certs/ca.pem",
                "-e",
                "PSModulePath=/vendor/modules:/opt/microsoft/powershell/7/Modules",
                "-e",
                "POWERSHELL_TELEMETRY_OPTOUT=1",
                "-e",
                "HTTP_PROXY=",
                "-e",
                "HTTPS_PROXY=",
                "-e",
                "ALL_PROXY=",
                "-e",
                "NO_PROXY=*",
                "--mount",
                f"type=bind,source={ca},target=/certs/ca.pem,readonly",
                "--mount",
                f"type=bind,source={ROOT / 'integrations'},target=/project/integrations,readonly",
                "--mount",
                f"type=bind,source={ROOT / 'work/cloud-e-vendor'},target=/vendor,readonly",
                "--mount",
                f"type=bind,source={state},target=/state",
                PS_IMAGE,
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-File",
                "/project/integrations/powershell/Test-GraphWorkflows.ps1",
                "-Profile",
                "/state/" + profile.name,
                "-Report",
                "/state/" + report.name,
                "-Mode",
                mode,
            ]
        if ecosystem != "powershell":
            args += ["--profile", str(profile), "--report", str(report), "--mode", mode]
        command(args, cwd=ROOT, timeout=300)
        result = json.loads(report.read_text("utf-8-sig"))
        assert result["passed"]
        check("graph_" + ecosystem + "_" + mode)
        return result

    output = state / "notifications.ndjson"
    statusfile = state / "receiver-status.txt"
    statusfile.write_text("202")

    def notifications():
        if not output.exists():
            return []
        lines = output.read_text("utf-8").splitlines()
        return [row for line in lines if line for row in json.loads(line)["value"]]

    def wait(predicate):
        for _ in range(120):
            if predicate():
                return
            time.sleep(0.25)
        raise RuntimeError("Local Graph delivery did not meet its expected outcome")

    report = {"passed": False, "localOnly": True, "written": {}, "restarted": {}}
    try:
        # Native subscription POST performs the actual TLS validation handshake.
        for ecosystem in ECOSYSTEMS:
            report["written"][ecosystem] = run(ecosystem, "write")
        fixtures = {e: v["objects"] for e, v in report["written"].items()}
        (state / "fixtures.json").write_text(json.dumps(fixtures, indent=2) + "\n", "utf-8")
        wait(
            lambda: all(
                any(
                    n.get("resourceData", {}).get("id") == f["message"]
                    and n.get("changeType") == "created"
                    and n.get("clientState") == e + "-local"
                    for n in notifications()
                )
                for e, f in fixtures.items()
            )
        )
        report["callback_delivery"] = True
        # Compare the same recurrence on the native Windows host and Linux runtime.
        from mockazure.calendar.recurrence import expand_occurrences

        body = {**profiles["python"]["event_body"], "id": "series", "iCalUId": "shared"}
        start = datetime(2026, 10, 30, tzinfo=UTC)
        end = datetime(2026, 11, 4, tzinfo=UTC)
        windows = expand_occurrences(body, start, end, 10)
        code = (
            "import json;from datetime import datetime,UTC;"
            "from mockazure.calendar.recurrence import expand_occurrences;"
            "p=json.load(open('/state/python.json'));"
            "b={**p['event_body'],'id':'series','iCalUId':'shared'};"
            "print(json.dumps(expand_occurrences(b,datetime(2026,10,30,tzinfo=UTC),"
            "datetime(2026,11,4,tzinfo=UTC),10)))"
        )
        linux = json.loads(
            command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--entrypoint",
                    "python",
                    "--mount",
                    f"type=bind,source={state},target=/state,readonly",
                    image,
                    "-c",
                    code,
                ],
                capture=True,
            )
        )
        assert windows == linux
        report["timezonePlatformEquivalence"] = True
        statusfile.write_text("503")
        target = fixtures["python"]
        bearer = {"Authorization": "Bearer " + token}

        def matching():
            return [
                n
                for n in notifications()
                if n.get("resourceData", {}).get("id") == target["message"]
                and n.get("changeType") == "updated"
            ]

        before = len(matching())
        response = client.patch(
            f"/v1.0/users/{target['owner']}/messages/{target['message']}",
            headers=bearer,
            json={"subject": "Retry across restart"},
        )
        assert response.status_code == 200
        wait(lambda: len(matching()) > before)
        retry_payload = matching()[before]
        command(compose + ["restart", "api", "worker"])
        ready()
        statusfile.write_text("202")
        wait(lambda: matching().count(retry_payload) >= 2)
        report["callback_retry"] = True
        report["callbackRetryPayloadStable"] = True
        for ecosystem in ECOSYSTEMS:
            report["restarted"][ecosystem] = run(ecosystem, "read")
        baseline = len(notifications())
        for f in fixtures.values():
            response = client.patch(
                f"/v1.0/users/{f['owner']}/messages/{f['message']}",
                headers=bearer,
                json={"subject": "After subscription deletion"},
            )
            assert response.status_code == 200
        time.sleep(1)
        assert len(notifications()) == baseline
        report["revoked_callbacks"] = True
        report["passed"] = True
        return report
    finally:
        (directory / "graph-workflows-report.json").write_text(
            json.dumps(report, indent=2) + "\n", "utf-8"
        )
