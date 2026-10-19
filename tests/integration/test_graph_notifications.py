"""Real local HTTPS callbacks validate persisted Graph subscriptions and worker delivery."""

import json
import ssl
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job
from mockazure.tls import ensure_tls
from tests.integration.graph_support import graph_client
from tests.integration.test_graph_send_workflows import setup_mail
from tests.integration.workflow_support import headers_for


@contextmanager
def receiver(tmp_path, database):
    state = {"notifications": [], "validations": [], "status": 202, "redirect": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            query = parse_qs(urlsplit(self.path).query)
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if "validationToken" in query:
                token = query["validationToken"][0]
                state["validations"].append(token)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(token.encode())
                return
            state["notifications"].append(json.loads(raw))
            self.send_response(302 if state["redirect"] else state["status"])
            if state["redirect"]:
                self.send_header("Location", state["redirect"])
            self.end_headers()
            if state.get("slow"):
                import time

                try:
                    for _ in range(8):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(0.05)
                except OSError:
                    pass

    certs = ensure_tls(tmp_path / "receiver", ("localhost",))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certs.server_cert, certs.server_key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"https://localhost:{server.server_port}/notifications"
    registry = {url: {"address": "127.0.0.1", "ca_file": str(certs.ca_cert)}}
    database.sessions.kw["info"]["notification_receivers"] = registry
    try:
        yield url, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def notification_worker(database, tenant, now=None, kind="graph.notification"):
    now = now or datetime.now(UTC)
    async with database.sessions.begin() as session:
        claim = await claim_job(
            session, owner=uuid4(), now=now, lease_seconds=30, tenant_id=tenant, kind=kind
        )
    assert claim is not None
    return await dispatch_job(database, claim, now)


async def test_subscription_validation_lifecycle(database, settings, tmp_path):
    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            _, write, _ = await setup_mail(client, database, settings, tenant, app, owner)
            read = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
            body = {
                "changeType": "created,updated,deleted",
                "resource": f"users/{owner}/messages",
                "notificationUrl": url,
                "lifecycleNotificationUrl": url,
                "expirationDateTime": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                "clientState": "local-callback-check",
            }
            created = await client.post("/v1.0/subscriptions", headers=read, json=body)
            assert created.status_code == 201, created.text
            sub = created.json()
            path = "/v1.0/subscriptions/" + sub["id"]
            assert len(state["validations"]) >= 1
            assert datetime.fromisoformat(
                sub["expirationDateTime"].replace("Z", "+00:00")
            ) > datetime.now(UTC) + timedelta(minutes=44)
            assert sub["applicationId"] == str(app)
            assert (
                await client.post("/v1.0/subscriptions", headers=read, json=body)
            ).status_code == 409
            assert (await client.get(path, headers=read)).status_code == 200
            assert len((await client.get("/v1.0/subscriptions", headers=read)).json()["value"]) == 1
            message = await client.post(
                f"/v1.0/users/{owner}/messages",
                headers=write,
                json={"subject": "Callback evidence"},
            )
            assert message.status_code == 201
            await database.engine.dispose()
            assert (await notification_worker(database, tenant)).state == "succeeded"
            note = state["notifications"][0]["value"][0]
            assert (
                note["clientState"] == body["clientState"] and note["subscriptionId"] == sub["id"]
            )
            assert (
                note["changeType"] == "created"
                and note["resourceData"]["id"] == message.json()["id"]
            )
            assert "subject" not in note["resourceData"] and note["tenantId"] == str(tenant)
            renewed = await client.patch(
                path,
                headers=read,
                json={"expirationDateTime": (datetime.now(UTC) + timedelta(hours=2)).isoformat()},
            )
            assert renewed.status_code == 200, renewed.text
            assert (
                await client.post(path + "/reauthorize", headers=read, json={})
            ).status_code == 204
            assert (await client.delete(path, headers=read)).status_code == 204
            assert (await client.get(path, headers=read)).status_code == 404


async def test_notification_retry_and_revoke(database, settings, tmp_path):
    from sqlalchemy import update

    from mockazure.db.job_model import Job

    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            _, write, _ = await setup_mail(client, database, settings, tenant, app, owner)
            read = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
            body = {
                "changeType": "created",
                "resource": f"users/{owner}/messages",
                "notificationUrl": url,
                "expirationDateTime": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
            created = await client.post("/v1.0/subscriptions", headers=read, json=body)
            assert created.status_code == 201, created.text
            await client.post(
                f"/v1.0/users/{owner}/messages", headers=write, json={"subject": "Retry"}
            )
            state["status"] = 503
            assert (await notification_worker(database, tenant)).state == "pending"
            async with database.sessions.begin() as session:
                await session.execute(
                    update(Job)
                    .where(
                        Job.tenant_id == tenant,
                        Job.kind == "graph.notification",
                        Job.state == "pending",
                    )
                    .values(available_at=datetime.now(UTC))
                )
            state["status"] = 202
            await database.engine.dispose()
            assert (await notification_worker(database, tenant)).state == "succeeded"
            assert len(state["notifications"]) == 2
            await client.post(
                f"/v1.0/users/{owner}/messages", headers=write, json={"subject": "Revoked"}
            )
            assert (
                await client.delete("/v1.0/subscriptions/" + created.json()["id"], headers=read)
            ).status_code == 204
            result = await notification_worker(database, tenant)
            assert result.state in {"failed", "stale"}
            assert len(state["notifications"]) == 2


async def test_receiver_boundary(database, settings, tmp_path):
    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            await setup_mail(client, database, settings, tenant, app, owner)
            read = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
            body = {
                "changeType": "created",
                "resource": f"users/{owner}/messages",
                "notificationUrl": "https://unregistered.invalid/callback",
                "expirationDateTime": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
            assert (
                await client.post("/v1.0/subscriptions", headers=read, json=body)
            ).status_code == 400
            assert not state["validations"]
            body["notificationUrl"] = url
            body["includeResourceData"] = True
            rich = await client.post("/v1.0/subscriptions", headers=read, json=body)
            assert rich.status_code == 501, rich.text
            assert not state["validations"]


async def subscribe(client, headers, url, resource, **extra):
    response = await client.post(
        "/v1.0/subscriptions",
        headers=headers,
        json={
            "changeType": "created,updated,deleted",
            "resource": resource,
            "notificationUrl": url,
            "lifecycleNotificationUrl": url,
            "expirationDateTime": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_calendar_and_drive_notifications(database, settings, tmp_path):
    from tests.integration.site_drive_support import seed_site
    from tests.integration.test_graph_calendar_workflows import event_body

    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            await setup_mail(client, database, settings, tenant, app, owner)
            cal = await headers_for(database, settings, tenant, app, None, {"Calendars.ReadWrite"})
            await subscribe(client, cal, url, f"users/{owner}/events")
            body = event_body()
            body["attendees"] = []
            created = await client.post(f"/v1.0/users/{owner}/events", headers=cal, json=body)
            assert created.status_code == 201, created.text
            assert (await notification_worker(database, tenant)).state == "succeeded"
            assert (
                state["notifications"][-1]["value"][0]["resourceData"]["id"] == created.json()["id"]
            )
            site = await seed_site(client, database, tenant, owner)
            files = await headers_for(database, settings, tenant, app, None, {"Files.Read.All"})
            await subscribe(
                client, files, url, f"drives/{site['drive']}/root", changeType="updated"
            )
            batch = site["batch"]
            batch["expected_revision"] = 1
            batch["records"][3]["payload"]["name"] = "Changed evidence.txt"
            updated = await client.post(
                f"/_mock/tenants/{tenant}/workloads/sharepoint/import", json=batch
            )
            assert updated.status_code == 200, updated.text
            assert (await notification_worker(database, tenant)).state == "succeeded"
            note = state["notifications"][-1]["value"][0]
            assert note["changeType"] == "updated"
            assert note["resource"] == f"drives/{site['drive']}/root"


async def test_supported_lifecycle_events(database, settings, tmp_path):
    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            _, write, _ = await setup_mail(client, database, settings, tenant, app, owner)
            read = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
            sub = await subscribe(client, read, url, f"users/{owner}/messages")
            path = f"/_mock/tenants/{tenant}/notifications/{sub['id']}/lifecycle"
            for event in ["reauthorizationRequired", "missed", "subscriptionRemoved"]:
                queued = await client.post(path, json={"event": event})
                assert queued.status_code == 202, queued.text
                assert (await notification_worker(database, tenant)).state == "succeeded"
                note = state["notifications"][-1]["value"][0]
                assert note["lifecycleEvent"] == event and "changeType" not in note
                assert "resourceData" not in note
            assert (
                await client.get("/v1.0/subscriptions/" + sub["id"], headers=read)
            ).status_code == 404
            assert len(state["notifications"]) == 3


async def test_registry_drift_and_permission_revocation_stop_callbacks(
    database, settings, tmp_path
):
    from sqlalchemy import update

    from mockazure.identity.models import Application

    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            _, write, _ = await setup_mail(client, database, settings, tenant, app, owner)
            read = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
            await subscribe(client, read, url, f"users/{owner}/messages")
            await client.post(
                f"/v1.0/users/{owner}/messages", headers=write, json={"subject": "Do not send"}
            )
            registry = database.sessions.kw["info"]["notification_receivers"]
            registry[url]["address"] = "127.0.0.2"
            assert (await notification_worker(database, tenant)).state == "failed"
            assert not state["notifications"]
            registry[url]["address"] = "127.0.0.1"
            await client.post(
                f"/v1.0/users/{owner}/messages",
                headers=write,
                json={"subject": "Disabled application"},
            )
            async with database.sessions.begin() as session:
                await session.execute(
                    update(Application).where(Application.client_id == app).values(enabled=False)
                )
            assert (await notification_worker(database, tenant)).state == "failed"
            assert not state["notifications"]


async def test_retry_beyond_generic_limit_and_redirect_boundary(database, settings, tmp_path):
    from sqlalchemy import update

    from mockazure.db.job_model import Job

    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            _, write, _ = await setup_mail(client, database, settings, tenant, app, owner)
            read = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
            await subscribe(client, read, url, f"users/{owner}/messages")
            await client.post(
                f"/v1.0/users/{owner}/messages", headers=write, json={"subject": "Retry four times"}
            )
            state["redirect"] = "https://external.invalid/callback"
            for _ in range(4):
                assert (await notification_worker(database, tenant)).state == "pending"
                async with database.sessions.begin() as session:
                    await session.execute(
                        update(Job)
                        .where(
                            Job.tenant_id == tenant,
                            Job.kind == "graph.notification",
                            Job.state == "pending",
                        )
                        .values(available_at=datetime.now(UTC))
                    )
            state["redirect"] = None
            assert (await notification_worker(database, tenant)).state == "succeeded"
            assert len(state["notifications"]) == 5


async def test_subscription_description_import_never_reactivates_receiver(
    database, settings, tmp_path
):
    from mockazure.mail.send import lock_workflow
    from mockazure.notifications.lifecycle import export_notifications, replace_notifications

    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            _, write, _ = await setup_mail(client, database, settings, tenant, app, owner)
            read = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
            await subscribe(
                client, read, url, f"users/{owner}/messages", clientState="private-state"
            )
            await client.post(
                f"/v1.0/users/{owner}/messages", headers=write, json={"subject": "Queued"}
            )
            async with database.sessions.begin() as session:
                await lock_workflow(session, tenant)
                snapshot = await export_notifications(session, tenant)
                encoded = snapshot.model_dump_json()
                assert (
                    url not in encoded and "private-state" not in encoded and "actor" not in encoded
                )
                await replace_notifications(session, tenant, snapshot)
                assert (
                    await export_notifications(session, tenant)
                ).model_dump() == snapshot.model_dump()
            assert (await client.get("/v1.0/subscriptions", headers=read)).json()["value"] == []
            async with database.sessions.begin() as session:
                assert (
                    await claim_job(
                        session,
                        owner=uuid4(),
                        now=datetime.now(UTC),
                        lease_seconds=30,
                        tenant_id=tenant,
                        kind="graph.notification",
                    )
                    is None
                )
            assert not state["notifications"]


async def test_expiry_and_stale_claim_prevent_http(database, settings, tmp_path):
    from sqlalchemy import update

    from mockazure.db.job_model import Job
    from mockazure.notifications.models import GraphSubscription

    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            _, write, _ = await setup_mail(client, database, settings, tenant, app, owner)
            read = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
            await subscribe(client, read, url, f"users/{owner}/messages")
            await client.post(
                f"/v1.0/users/{owner}/messages", headers=write, json={"subject": "Stale lease"}
            )
            now = datetime.now(UTC)
            async with database.sessions.begin() as session:
                claim = await claim_job(
                    session,
                    owner=uuid4(),
                    now=now,
                    lease_seconds=1,
                    tenant_id=tenant,
                    kind="graph.notification",
                )
            assert (
                await dispatch_job(database, claim, now + timedelta(seconds=2))
            ).state == "stale"
            assert not state["notifications"]
            async with database.sessions.begin() as session:
                await session.execute(
                    update(GraphSubscription)
                    .where(GraphSubscription.tenant_id == tenant)
                    .values(expires_at=now - timedelta(seconds=1))
                )
                await session.execute(
                    update(Job)
                    .where(Job.id == claim.id)
                    .values(lease_until=now - timedelta(seconds=1))
                )
            assert (await notification_worker(database, tenant)).state == "failed"
            assert not state["notifications"]


async def test_renewal_keeps_queued_change_but_callback_update_fences_it(
    database, settings, tmp_path
):
    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            _, write, _ = await setup_mail(client, database, settings, tenant, app, owner)
            read = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
            sub = await subscribe(client, read, url, f"users/{owner}/messages")
            path = "/v1.0/subscriptions/" + sub["id"]
            await client.post(
                f"/v1.0/users/{owner}/messages", headers=write, json={"subject": "Renewed"}
            )
            assert (
                await client.patch(
                    path,
                    headers=read,
                    json={
                        "expirationDateTime": (datetime.now(UTC) + timedelta(hours=3)).isoformat()
                    },
                )
            ).status_code == 200
            assert (await notification_worker(database, tenant)).state == "succeeded"
            await client.post(
                f"/v1.0/users/{owner}/messages",
                headers=write,
                json={"subject": "Old endpoint generation"},
            )
            assert (
                await client.patch(path, headers=read, json={"notificationUrl": url})
            ).status_code == 200
            assert (await notification_worker(database, tenant)).state == "failed"
            assert len(state["notifications"]) == 1


async def test_four_hour_delivery_deadline_emits_missed(database, settings, tmp_path):
    from sqlalchemy import update

    from mockazure.notifications.models import GraphNotification

    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            _, write, _ = await setup_mail(client, database, settings, tenant, app, owner)
            read = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
            await subscribe(client, read, url, f"users/{owner}/messages")
            await client.post(
                f"/v1.0/users/{owner}/messages", headers=write, json={"subject": "Expired retry"}
            )
            async with database.sessions.begin() as session:
                await session.execute(
                    update(GraphNotification)
                    .where(GraphNotification.tenant_id == tenant)
                    .values(deadline=datetime.now(UTC) - timedelta(seconds=1))
                )
            assert (await notification_worker(database, tenant)).state == "failed"
            assert not state["notifications"]
            assert (await notification_worker(database, tenant)).state == "succeeded"
            assert state["notifications"][0]["value"][0]["lifecycleEvent"] == "missed"


async def test_notification_migration_and_cloud_reset(database, settings, tmp_path):
    import pytest
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError

    from mockazure.db.migrations import configuration

    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            _, write, _ = await setup_mail(client, database, settings, tenant, app, owner)
            engine = create_engine(settings.database_url.get_secret_value())
            try:
                config = configuration()
                with engine.begin() as connection:
                    config.attributes["connection"] = connection
                    command.downgrade(config, "070")
                    command.upgrade(config, "head")
                read = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
                await subscribe(client, read, url, f"users/{owner}/messages")
                await client.post(
                    f"/v1.0/users/{owner}/messages",
                    headers=write,
                    json={"subject": "Pending reset"},
                )
                with pytest.raises(DBAPIError, match="Reset subscriptions before downgrade"):
                    with engine.begin() as connection:
                        config.attributes["connection"] = connection
                        command.downgrade(config, "070")
                cloud = f"/_mock/tenants/{tenant}/cloud/state"
                snapshot = (await client.get(cloud)).json()
                assert len(snapshot["notifications"]["subscriptions"]) == 1
                reset = await client.post(
                    f"/_mock/tenants/{tenant}/cloud/reset",
                    json={"expected_generation": snapshot["generation"]},
                )
                assert reset.status_code == 200, reset.text
                assert (await client.get(cloud)).json()["notifications"]["subscriptions"] == []
                assert (await client.get("/v1.0/subscriptions", headers=read)).json()["value"] == []
                assert not state["notifications"]
            finally:
                engine.dispose()


async def test_notification_parent_tenant_is_database_enforced(database, settings, tmp_path):
    import pytest
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from mockazure.db.models import Tenant
    from mockazure.notifications.models import GraphNotification

    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            _, write, _ = await setup_mail(client, database, settings, tenant, app, owner)
            read = await headers_for(database, settings, tenant, app, None, {"Mail.Read"})
            await subscribe(client, read, url, f"users/{owner}/messages")
            await client.post(
                f"/v1.0/users/{owner}/messages", headers=write, json={"subject": "Tenant fence"}
            )
            other = uuid4()
            async with database.sessions.begin() as session:
                session.add(
                    Tenant(
                        id=other,
                        slug="other",
                        display_name="Other",
                        domain="other.test",
                        licences=[],
                    )
                )
            with pytest.raises(IntegrityError):
                async with database.sessions.begin() as session:
                    note = await session.scalar(
                        select(GraphNotification).where(GraphNotification.tenant_id == tenant)
                    )
                    note.tenant_id = other
                    await session.flush()


async def test_receiver_total_timeout_bounds_slow_response(database, tmp_path):
    import pytest

    from mockazure.notifications.receivers import post_receiver, resolve_receiver

    with receiver(tmp_path, database) as (url, state):
        state["slow"] = True
        binding = resolve_receiver(url, database.sessions.kw["info"]["notification_receivers"])
        with pytest.raises(TimeoutError):
            await post_receiver(binding, payload={"value": []}, timeout=0.12)


async def test_long_drive_identity_does_not_break_source_import(database, settings, tmp_path):
    from tests.integration.site_drive_support import seed_site

    with receiver(tmp_path, database) as (url, state):
        async with graph_client(database, settings) as (client, tenant, app, owner):
            fixture = await seed_site(client, database, tenant, owner, suffix="d" * 300)
            headers = await headers_for(database, settings, tenant, app, None, {"Files.Read.All"})
            await subscribe(
                client, headers, url, f"drives/{fixture['drive']}/root", changeType="updated"
            )
            batch = fixture["batch"]
            batch["expected_revision"] = 1
            batch["records"][3]["payload"]["name"] = "Updated long identity"
            response = await client.post(
                f"/_mock/tenants/{tenant}/workloads/sharepoint/import", json=batch
            )
            assert response.status_code == 200, response.text
            assert (await notification_worker(database, tenant)).state == "succeeded"
            assert (
                state["notifications"][0]["value"][0]["resource"]
                == f"drives/{fixture['drive']}/root"
            )
