from mockazure.population.corpus.models import ContentSpec
from mockazure.population.corpus.render import render
from mockazure.population.generators import generate, inventory

PARAMETERS = {
    "tenant": "11111111-1111-4111-8111-111111111111",
    "namespace": "business",
    "users": 4,
    "projects": 2,
    "days": 7,
    "seed": 42,
    "referenceTime": "2026-10-20T12:00:00Z",
}


def values(name, count, offset=0, limit=1000, **parameters):
    return generate(
        "business." + name,
        {**PARAMETERS, "count": count, **parameters},
        {"domain": "contoso.test"},
        offset=offset,
        limit=limit,
    )


def test_conversation_has_matching_sender_recipient_copies():
    sent, received, reply_sent, reply_received = values("messages", 4)
    assert sent.parent != received.parent
    first, second = sent.payload["message"], received.payload["message"]
    assert first["id"] != second["id"]
    assert first["folder_id"] == "sentitems" and second["folder_id"] == "inbox"
    for field in ("internetMessageId", "conversationId", "from", "toRecipients", "subject"):
        assert first["payload"][field] == second["payload"][field]
    assert reply_sent.parent == received.parent and reply_received.parent == sent.parent
    assert (
        reply_sent.payload["message"]["payload"]["conversationId"]
        == first["payload"]["conversationId"]
    )
    headers = reply_sent.payload["message"]["payload"]["internetMessageHeaders"]
    assert {"name": "In-Reply-To", "value": first["payload"]["internetMessageId"]} in headers


def test_invitation_links_attendee_and_organizer_events():
    organizer, attendee = values("events", 2)
    assert organizer.parent != attendee.parent
    a, b = organizer.payload, attendee.payload
    assert a["meeting_id"] == b["meeting_id"]
    assert a["organizer_id"] == b["organizer_id"] == organizer.parent
    assert a["payload"]["iCalUId"] == b["payload"]["iCalUId"]
    assert a["payload"]["isOrganizer"] and not b["payload"]["isOrganizer"]
    assert a["payload"]["recurrence"] == b["payload"]["recurrence"]
    assert a["exceptions"] == b["exceptions"]
    assert a["exceptions"]


def test_attachment_bytes_match_document_revision():
    first, _, second, _ = values("messages", 4)
    documents = values("documents", 2)
    versions = values("versions", 2)
    assert first.payload["document"] == versions[0].payload["document"]
    assert second.payload["document"] == documents[0].payload["document"]
    for spec in (first.payload["document"], second.payload["document"]):
        rendered = render(ContentSpec.model_validate(spec))
        assert rendered.length and rendered.sha256


def test_channel_files_folder_resolves_canonical_drive_item():
    reference = values("files-folders", 6)[0]
    folder = values("folders", 2)[0]
    drive = values("drives", 2)[0]
    assert reference.payload["payload"]["itemId"] == folder.payload["payload"]["id"]
    assert reference.payload["payload"]["driveId"] == drive.payload["payload"]["id"]


def test_all_business_generators_are_bounded_chunk_independent_and_schema_valid():
    from mockazure.population.catalog import get_adapter
    from mockazure.population.generators.business import FAMILIES

    assert "business.messages" in inventory()
    for name, entry in inventory().items():
        if name not in {"business." + family for family in FAMILIES}:
            continue
        count = 2
        whole = values(name.removeprefix("business."), count)
        chunks = [
            values(name.removeprefix("business."), count, offset=i, limit=1)[0]
            for i in range(count)
        ]
        assert whole == chunks, name
        adapter = get_adapter(entry["family"])
        for mutation in whole:
            adapter.validate(mutation)
        assert entry["version"] == "business-v1"


def test_profile_variants_scale_per_user_or_project_without_count_arithmetic():
    folders = values("mail-folders", 4, folder="sentitems")
    assert all(item.payload["id"] == "sentitems" for item in folders)
    assert len({item.parent for item in folders}) == 4
    channels = values("channels", 2, channel_kind="private")
    assert all(item.payload["payload"]["membershipType"] == "private" for item in channels)
    assert len({item.parent for item in channels}) == 2
    first = values("team-members", 2, member_slot=0)
    second = values("team-members", 2, member_slot=1)
    assert [m.parent for m in first] == [m.parent for m in second]
    assert all(a.logical_key != b.logical_key for a, b in zip(first, second, strict=True))


def test_collaboration_layers_stack_on_organization_with_bounded_counts():
    from datetime import UTC, datetime

    from mockazure.population.catalog import get_adapter
    from mockazure.population.planner import mutations_for
    from mockazure.profiles.builtin import builtin_bundle
    from mockazure.profiles.models import ProfileDocument
    from mockazure.profiles.resolver import resolve_template

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
    resolved = resolve_template(
        bundle,
        "connected-test",
        {"shared.users": 1, "shared.projects": 1},
        datetime(2026, 10, 20, tzinfo=UTC),
    )
    assert resolved.steps["users"].parameters["generator"] == "business.users"
    snapshot = {
        "resolved": resolved.model_dump(mode="json"),
        "tenant": PARAMETERS["tenant"],
        "request": {"dataset_namespace": "stack"},
    }
    refs = {"domain": "contoso.test", "primary-user": "22222222-2222-4222-8222-222222222222"}
    for step in snapshot["resolved"]["steps"].values():
        if not step["parameters"]["generator"].startswith("business."):
            continue
        generated = mutations_for(snapshot, step, refs, 0, 1000)
        assert len(generated) == step["parameters"]["count"]
        for mutation in generated:
            get_adapter(step["family"]).validate(mutation)


def test_single_user_has_no_duplicate_member_or_false_attendee_copies():
    first = values("team-members", 1, users=1, projects=1, member_slot=0)[0]
    second = values("team-members", 1, users=1, projects=1, member_slot=1)[0]
    assert first.payload == second.payload
    events = values("events", 2, users=1)
    assert all(event.payload["payload"]["isOrganizer"] for event in events)
    assert all(event.payload["payload"]["attendees"] == [] for event in events)
    assert events[0].payload["meeting_id"] != events[1].payload["meeting_id"]


def test_connected_mail_rejects_invalid_mime_inputs_before_apply():
    import copy

    import pytest

    from mockazure.population.catalog import get_adapter

    original = values("messages", 2)[0]
    for field, value in (
        ("subject", "Injected\r\nBcc: other@lab.test"),
        ("sentDateTime", "2026-10-01T00:00:00"),
        ("toRecipients", []),
    ):
        payload = copy.deepcopy(original.payload)
        payload["message"]["payload"][field] = value
        with pytest.raises(ValueError):
            get_adapter("mail.messages").validate(original.model_copy(update={"payload": payload}))


def test_message_history_keeps_valid_edit_and_deletion_timestamps():
    from datetime import datetime

    edited = values("channel-messages", 4)[1].payload["payload"]
    deleted = values("chat-messages", 4)[1].payload["payload"]
    assert datetime.fromisoformat(edited["lastEditedDateTime"]) > datetime.fromisoformat(
        edited["createdDateTime"]
    )
    assert deleted["body"]["content"] == ""
    assert deleted["attachments"] == [] and deleted["mentions"] == []
    assert datetime.fromisoformat(deleted["deletedDateTime"]) > datetime.fromisoformat(
        deleted["createdDateTime"]
    )
