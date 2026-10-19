"""Operation-specific Graph v1.0 mail contracts are pinned, closed and typed."""

import pytest

from mockazure.cloud.contracts import load_contract, verify_references
from mockazure.mail.workflow_contracts import validate_mail_action
from mockazure.providers.graph.errors import GraphError


def test_graph_workflow_source_closure():
    verify_references("graph-workflows")
    for operation in (
        "user.sendMail",
        "message.send",
        "message.createReply",
        "message.forward",
        "message.createUploadSession",
    ):
        record = load_contract("graph-workflows", operation)
        assert record["version"] == "v1.0"
        assert len(record["sources"]) >= 2


@pytest.mark.parametrize(
    "action,body",
    [
        ("sendMail", {"message": {"subject": "test"}, "saveToSentItems": "false"}),
        ("sendMail", {"message": {"id": "injected"}}),
        (
            "reply",
            {"comment": "one", "message": {"body": {"contentType": "text", "content": "two"}}},
        ),
        ("forward", {"toRecipients": [], "message": {"toRecipients": []}}),
        ("create", {"subject": "x", "serverInjected": True}),
        (
            "createUploadSession",
            {"AttachmentItem": {"attachmentType": "item", "name": "x", "size": 4000000}},
        ),
    ],
)
def test_graph_action_rejects_wrong_types_or_conflicting_fields(action, body):
    with pytest.raises(GraphError):
        validate_mail_action(action, body)


def test_graph_action_preserves_real_wire_shape():
    body = {
        "message": {"subject": "x", "body": {"contentType": "text", "content": "value"}},
        "saveToSentItems": False,
    }
    assert validate_mail_action("sendMail", body) == body
    assert validate_mail_action("createReply", {}) == {}


@pytest.mark.parametrize(
    "body",
    [
        {"subject": "bad\r\nheader"},
        {"internetMessageId": "bad\nheader"},
        {"attachments": [{"@odata.type": 42, "name": "bad", "contentBytes": ""}]},
    ],
)
def test_header_and_discriminator_errors_are_sanitized(body):
    with pytest.raises(GraphError):
        validate_mail_action("create", body)


@pytest.mark.parametrize("field", ["name", "contentType", "contentId"])
def test_upload_session_rejects_header_injection_before_acceptance(field):
    item = {"attachmentType": "file", "name": "evidence.bin", "size": 3145728}
    item[field] = "bad\r\nheader"
    with pytest.raises(GraphError):
        validate_mail_action("createUploadSession", {"AttachmentItem": item})


def test_mail_operation_schemas_resolve_to_actual_types_and_upload_methods():
    import json

    from mockazure.cloud.contracts import DATA
    from mockazure.mail.workflow_contracts import references

    types = references()["types"]
    actions = {a["name"] for a in references()["actions"]}
    operations = json.loads((DATA / "graph-workflows/operations.json").read_text("utf-8"))
    for operation in operations["operations"]:
        request = operation.get("requestSchema")
        if request and request.get("type") == "resource":
            assert request["name"] in types
        if request and request.get("type") == "action":
            assert request["name"] in actions
    for method in ("PUT", "GET", "DELETE"):
        upload = load_contract("graph-workflows", "attachmentUpload." + method.lower())
        assert upload["method"] == method
        assert any(
            s["localPath"].endswith("outlook-large-attachments.md") for s in upload["sources"]
        )


def test_calendar_meeting_message_is_a_real_importable_graph_type():
    from mockazure.mail.content_models import MessageImport

    imported = MessageImport(
        id="message",
        folder_id="inbox",
        payload={
            "@odata.type": "#microsoft.graph.eventMessage",
            "subject": "Meeting",
            "meetingMessageType": "meetingRequest",
            "isDraft": False,
        },
    )
    assert imported.payload["meetingMessageType"] == "meetingRequest"


@pytest.mark.parametrize("resource", ["calendarGroup", "calendar", "event"])
def test_calendar_catalog_describes_collection_and_item_envelopes(resource):
    collection = load_contract("graph-workflows", resource + ".list")
    response = collection["responseSchemas"]["200"]
    assert response["type"] == "object"
    assert response["properties"]["value"]["items"] == {"$ref": "schema.json#/types/" + resource}
    item = load_contract("graph-workflows", resource + ".get")
    assert item["responseSchemas"]["200"] == {"$ref": "schema.json#/types/" + resource}
    assert item["queryOptions"] == ["$select"]


def test_calendar_accepts_exact_sdk_type_discriminators():
    from mockazure.calendar.contracts import event_input

    body = {
        "@odata.type": "#microsoft.graph.event",
        "subject": "Typed calendar",
        "start": {
            "@odata.type": "#microsoft.graph.dateTimeTimeZone",
            "dateTime": "2026-10-25T10:00:00",
            "timeZone": "UTC",
        },
        "end": {
            "@odata.type": "#microsoft.graph.dateTimeTimeZone",
            "dateTime": "2026-10-25T11:00:00",
            "timeZone": "UTC",
        },
        "attendees": [
            {
                "@odata.type": "#microsoft.graph.attendee",
                "type": "required",
                "emailAddress": {"address": "local@contoso.test"},
            }
        ],
    }
    assert event_input(body)["attendees"][0]["emailAddress"]["address"] == "local@contoso.test"
