import hashlib
from email import policy
from email.parser import BytesParser

from mockazure.population.catalog import get_adapter
from mockazure.population.generators import inventory
from mockazure.population.types import AdapterContext, Mutation
from tests.integration.graph_support import graph_client, headers_for
from tests.unit.test_business_relationships import values

STEPS = [
    ("users", 4),
    ("mailboxes", 4),
    ("mail-folders", 12),
    ("sites", 2),
    ("drives", 2),
    ("roots", 2),
    ("folders", 2),
    ("documents", 2),
    ("versions", 2),
    ("file-permissions", 2),
    ("site-access", 2),
    ("teams", 2),
    ("channels", 6),
    ("team-members", 4),
    ("channel-members", 6),
    ("channel-messages", 4),
    ("channel-replies", 4),
    ("files-folders", 6),
    ("chats", 2),
    ("chat-members", 4),
    ("chat-messages", 4),
    ("messages", 4),
    ("events", 2),
]


async def populate(database, settings, tenant):
    ctx = AdapterContext(database, settings, tenant)
    await get_adapter("mail.settings").apply(
        ctx,
        [
            Mutation(
                operation="provision",
                logical_key="settings",
                payload={"id": str(tenant), "exchange_enabled": True, "audit_tier": "premium"},
            )
        ],
    )
    results = {}
    for name, count in STEPS:
        adapter = get_adapter(inventory()["business." + name]["family"])
        mutations = values(name, count, tenant=str(tenant))
        results[name] = await adapter.apply(ctx, mutations)
    return ctx, results


async def test_namespace_replay_preserves_counts_and_ids(database, settings):
    async with graph_client(database, settings) as (_, tenant, *_):
        _, before = await populate(database, settings, tenant)
        _, after = await populate(database, settings, tenant)
        for name, _ in STEPS:
            assert [item.id for item in before[name].items] == [
                item.id for item in after[name].items
            ]
            assert after[name].effects["created"] == 0
            assert after[name].effects["updated"] == 0


async def test_public_mail_mime_attachment_and_drive_recover_matching_content(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        _, results = await populate(database, settings, tenant)
        headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            [
                "Mail.Read",
                "Files.Read.All",
                "Calendars.Read",
                "ChannelMessage.Read.All",
            ],
        )
        source = values("messages", 4, tenant=str(tenant))[2]
        message_id = source.payload["message"]["id"]
        base = f"/v1.0/users/{source.parent}/messages/{message_id}"
        message = await client.get(base, headers=headers)
        assert message.status_code == 200, message.text
        assert message.json()["hasAttachments"]
        attachments = await client.get(base + "/attachments", headers=headers)
        assert attachments.status_code == 200, attachments.text
        attachment = attachments.json()["value"][0]
        download = await client.get(
            base + f"/attachments/{attachment['id']}/$value", headers=headers
        )
        assert download.status_code == 200, download.text
        drive = results["drives"].items[0].id
        document = results["documents"].items[0].id
        file = await client.get(
            f"/v1.0/drives/{drive}/items/{document}/content",
            headers=headers,
            follow_redirects=True,
        )
        assert file.status_code == 200, file.text
        assert file.content == download.content
        mime = await client.get(base + "/$value", headers=headers)
        assert mime.status_code == 200, mime.text
        parsed = BytesParser(policy=policy.default).parsebytes(mime.content)
        part = next(parsed.iter_attachments())
        assert part.get_payload(decode=True) == file.content
        assert hashlib.sha256(file.content).hexdigest()
        meeting = results["events"].items[0]
        event = await client.get(
            f"/v1.0/users/{meeting.parent}/events/{meeting.id}", headers=headers
        )
        assert event.status_code == 200, event.text
        assert event.json()["recurrence"]


async def test_imported_private_membership_is_not_operational_authority(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        _, results = await populate(database, settings, tenant)
        user = results["users"].items[0].id
        headers = await headers_for(
            database, settings, tenant, app, user, ["ChannelMessage.Read.All"], kind="delegated"
        )
        team = values("teams", 2, tenant=str(tenant))[0].payload["id"]
        channel = values("channels", 6, tenant=str(tenant))[1].payload["payload"]["id"]
        response = await client.get(
            f"/v1.0/teams/{team}/channels/{channel}/messages", headers=headers
        )
        assert response.status_code in {403, 404}


async def test_project_file_access_requires_explicit_site_grant(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, outsider):
        _, results = await populate(database, settings, tenant)
        drive, item = results["drives"].items[0].id, results["documents"].items[0].id
        owner = results["users"].items[0].id
        allowed = await headers_for(
            database, settings, tenant, app, owner, ["Files.Read"], kind="delegated"
        )
        denied = await headers_for(
            database, settings, tenant, app, outsider, ["Files.Read"], kind="delegated"
        )
        path = f"/v1.0/drives/{drive}/items/{item}"
        assert (await client.get(path, headers=allowed)).status_code == 200
        assert (await client.get(path, headers=denied)).status_code in {403, 404}


async def test_business_runner_records_service_bindings_and_fences_retired_namespace(
    database, settings
):
    from datetime import UTC, datetime
    from uuid import UUID

    from mockazure.population.bindings import resolve, retire_namespace
    from mockazure.population.planner import RunRequest, preview
    from mockazure.population.repository import save_bundle, submit_run
    from mockazure.profiles.models import ProfileBundle, ProfileDocument
    from tests.integration.test_population_runs import drive

    async with graph_client(database, settings) as (_, tenant, *_):
        base = ProfileDocument.model_validate(
            {
                "schemaVersion": 1,
                "kind": "tenant",
                "name": "organization",
                "steps": {
                    "users": {
                        "family": "directory.users",
                        "operation": "import",
                        "mode": "fixture",
                        "parameters": {"generator": "business.users", "count": 2},
                    }
                },
            }
        )
        template = ProfileDocument.model_validate(
            {
                "schemaVersion": 1,
                "kind": "template",
                "name": "business-run",
                "tenantProfile": "organization",
            }
        )
        saved = await save_bundle(
            database,
            ProfileBundle(
                documents={
                    base.name: base,
                    template.name: template,
                }
            ),
        )
        request = RunRequest.model_validate(
            {
                "bundle_id": str(saved["id"]),
                "template": template.name,
                "target": {"existing_tenant_id": str(tenant)},
                "dataset_namespace": "business-run",
            }
        )
        p = await preview(database, request, datetime.now(UTC))
        run = await submit_run(database, p.id, p.digest, datetime.now(UTC))
        assert (await drive(database, run.id)).state == "succeeded"
        async with database.sessions() as session:
            row = await resolve(
                session, tenant, request.dataset_namespace, "business.users:0", "graph"
            )
            assert UUID(row.actual_id)
        p = await preview(database, request, datetime.now(UTC))
        run = await submit_run(database, p.id, p.digest, datetime.now(UTC))
        async with database.sessions.begin() as session:
            await retire_namespace(
                session, tenant, request.dataset_namespace, expected_generation=row.generation
            )
        outcome = await drive(database, run.id)
        assert outcome.state == "failed"
        assert "generation" in str(outcome.errors).lower()


async def test_stacked_collaboration_profile_runs_and_replays(database, settings):
    from datetime import UTC, datetime

    from mockazure.population.planner import RunRequest, preview
    from mockazure.population.repository import save_bundle, submit_run
    from mockazure.profiles.builtin import builtin_bundle
    from mockazure.profiles.models import ProfileDocument
    from tests.integration.test_population_runs import drive

    async with graph_client(database, settings) as (_, tenant, *_):
        bundle = builtin_bundle()
        bundle.documents["connected-test"] = ProfileDocument.model_validate(
            {
                "schemaVersion": 1,
                "kind": "template",
                "name": "connected-test",
                "tenantProfile": "small-business",
                "dataProfiles": ["business-mail-calendar", "business-sharepoint-teams"],
            }
        )
        saved = await save_bundle(database, bundle)
        request = RunRequest.model_validate(
            {
                "bundle_id": str(saved["id"]),
                "template": "connected-test",
                "target": {"existing_tenant_id": str(tenant)},
                "dataset_namespace": "connected",
                "parameters": {
                    "shared.users": 1,
                    "shared.projects": 1,
                    "business-mail-calendar.messageCopies": 4,
                    "business-mail-calendar.eventCopies": 2,
                },
            }
        )
        for attempt in range(2):
            p = await preview(database, request, datetime.now(UTC))
            run = await submit_run(database, p.id, p.digest, datetime.now(UTC))
            outcome = await drive(database, run.id, limit=200)
            assert outcome.state == "succeeded", outcome.errors
            if attempt:
                assert outcome.progress["created"] == 0
