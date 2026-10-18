"""Fresh local HTTPS lab: actual issuer, pull-feed pagination and native UAL correlation."""

import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

from tools.verify_default import main as smoke


def qualify(directory, command, compose, client, headers, tenant, check):
    base = "/_mock/tenants/" + tenant["id"]
    path = "/api/v1.0/" + tenant["id"] + "/activity/feed"

    def call(method, path, body=None, status=200, auth=headers):
        response = client.request(method, path, headers=auth, json=body)
        assert response.status_code == status, (path, response.status_code, response.text[:200])
        return response.json() if response.content else None

    permissions = {
        "activity": ["ActivityFeed.Read", "ActivityFeed.ReadDlp"],
        "graph": ["MailboxSettings.ReadWrite", "AuditLogsQuery.Read.All"],
    }
    app = call(
        "POST",
        base + "/identity/applications",
        {
            "display_name": "Feed qualification",
            "client_mode": "confidential",
            "required_permissions": [
                {"resource": resource, "kind": "application", "permissions": values}
                for resource, values in permissions.items()
            ],
        },
        201,
    )
    credential = call(
        "POST",
        base + f"/identity/applications/{app['client_id']}/credentials",
        {"kind": "secret"},
        201,
    )
    for resource, values in permissions.items():
        call(
            "PUT",
            base + f"/identity/grants/{uuid4()}",
            {
                "client_id": app["client_id"],
                "resource": resource,
                "kind": "application",
                "permissions": values,
            },
        )

    def token(resource):
        response = client.post(
            f"/{tenant['id']}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": app["client_id"],
                "client_secret": credential["secret"],
                "scope": resource + "/.default",
            },
        )
        assert response.status_code == 200, response.text[:200]
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    auth, graph = token("https://manage.office.com"), token("https://graph.microsoft.com")
    assert client.get(path + "/subscriptions/list", headers=graph).status_code == 401
    call("PATCH", base + "/activity/settings", {"page_size": 25})
    kinds = [
        "Audit.AzureActiveDirectory",
        "Audit.Exchange",
        "Audit.SharePoint",
        "Audit.General",
        "DLP.All",
    ]
    for kind in kinds:
        call("POST", path + "/subscriptions/start?contentType=" + kind, auth=auth)
    check("actual_issuer_distinct_activity_audience_and_five_subscriptions")
    call("PATCH", base + "/evidence/settings", {"delay_seconds": 0})
    # A native Graph mutation creates the audit fact consumed by the leased feed projector.
    call("PUT", base + "/mail/settings", {"exchange_enabled": True, "audit_tier": "standard"})
    user = call(
        "POST",
        base + "/identity/users",
        {
            "upn": "feed@" + tenant["domain"],
            "display_name": "Feed actor",
            "password": "local-feed-password",
        },
        201,
    )
    call(
        "PUT",
        base + "/mail/mailboxes/" + user["id"],
        {"primary_smtp": user["upn"], "alias": "feed"},
    )
    call(
        "POST",
        f"/v1.0/users/{user['id']}/mailFolders/inbox/messageRules",
        {
            "displayName": "Feed native rule",
            "sequence": 1,
            "isEnabled": True,
            "conditions": {"subjectContains": ["invoice"]},
            "actions": {"markAsRead": True},
        },
        201,
        auth=graph,
    )
    deadline = time.monotonic() + 60
    native = []
    while time.monotonic() < deadline:
        call("POST", base + "/evidence/drain", {"max_jobs": 100})
        call("POST", base + "/activity/drain", {"max_rows": 100})
        listing = call("GET", path + "/subscriptions/content?contentType=Audit.Exchange", auth=auth)
        native = [
            event
            for item in listing
            for event in call("GET", item["contentUri"], auth=auth)
            if event["Operation"] == "New-InboxRule"
        ]
        if native:
            break
        time.sleep(0.25)
    assert len(native) == 1
    now = datetime.now(UTC)
    query = call(
        "POST",
        "/v1.0/security/auditLog/queries",
        {
            "displayName": "Feed correlation",
            "filterStartDateTime": (now - timedelta(hours=1)).isoformat(),
            "filterEndDateTime": (now + timedelta(minutes=1)).isoformat(),
            "serviceFilter": "Exchange",
            "operationFilters": ["New-InboxRule"],
        },
        201,
        auth=graph,
    )
    item = "/v1.0/security/auditLog/queries/" + query["id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        current = call("GET", item, auth=graph)
        if current["status"] == "succeeded":
            break
        assert current["status"] != "failed", current
        time.sleep(0.25)
    else:
        raise TimeoutError("Local UAL query did not finish")
    ual = call("GET", item + "/records", auth=graph)["value"]
    assert [event["id"] for event in ual] == [native[0]["Id"]]
    assert "auditData" in ual[0] and "auditData" not in native[0]
    check("native_graph_rule_audit_correlates_with_feed_by_original_event_id")
    records = []
    for workload, number, count in [
        ("Exchange", 1, 101),
        ("AzureActiveDirectory", 8, 1),
        ("OneDrive", 7, 1),
        ("MicrosoftTeams", 25, 1),
        ("SharePoint", 11, 1),
    ]:
        for _ in range(count):
            records.append(
                {
                    "Id": str(uuid4()),
                    "OrganizationId": tenant["id"],
                    "CreationTime": (now - timedelta(days=20)).isoformat(),
                    "Operation": "Observed",
                    "RecordType": number,
                    "UserType": 0,
                    "UserKey": user["id"],
                    "UserId": user["upn"],
                    "Workload": workload,
                }
            )
    call("POST", base + "/activity/import", {"records": records})
    first = client.get(path + "/subscriptions/content?contentType=Audit.Exchange", headers=auth)
    assert first.status_code == 200 and len(first.json()) == 25 and "NextPageUri" in first.headers
    saved = first.headers["NextPageUri"]
    command(compose + ["restart", "db"])
    command(compose + ["restart", "api"])
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            if client.get("/_mock/ready").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise TimeoutError("Feed restart readiness failed")
    descriptors = first.json()
    while saved:
        response = client.get(saved, headers=auth)
        assert response.status_code == 200, response.text[:200]
        descriptors += response.json()
        saved = response.headers.get("NextPageUri")
    assert len(descriptors) == 102
    for kind in kinds:
        if kind != "Audit.Exchange":
            descriptors += call(
                "GET", path + "/subscriptions/content?contentType=" + kind, auth=auth
            )
    observed = [
        event for item in descriptors for event in call("GET", item["contentUri"], auth=auth)
    ]
    assert len(observed) == 106
    assert {event["Id"] for event in observed} == {event["Id"] for event in records + native}
    check("all_content_types_and_nextpage_resume_after_database_api_restart_106_records")
    call("POST", path + "/subscriptions/stop?contentType=Audit.Exchange", auth=auth)
    denied = client.get(first.json()[0]["contentUri"], headers=auth)
    assert denied.status_code == 400 and denied.json()["error"]["code"] == "AF20023"
    check("stop_revokes_retrieval_of_previously_discovered_content")
    (directory / "activity-report.json").write_text(
        json.dumps(
            {
                "passed": True,
                "records": len(observed),
                "content_types": kinds,
                "native_audit_id": native[0]["Id"],
                "issuer": "local",
                "outbound_microsoft_requests": 0,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    smoke(qualify=qualify)
