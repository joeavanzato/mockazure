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
