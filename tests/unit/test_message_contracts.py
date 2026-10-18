from uuid import uuid4

import pytest
from pydantic import ValidationError

from mockazure.mail.content_models import MailContentImport
from mockazure.providers.graph.schema_reference import schema


def test_mail_types_use_pinned_v1_schema():
    assert {
        "mailFolder",
        "message",
        "fileAttachment",
        "itemAttachment",
        "referenceAttachment",
        "mailboxSettings",
    } <= set(schema("v1.0")["types"])
    assert "body" in schema("v1.0")["types"]["message"]["properties"]


def test_mail_import_rejects_invented_message_fields_and_wrong_types():
    base = {"mailbox_user_id": str(uuid4()), "mode": "content-backed", "expected_revision": 0}
    for payload in (
        {"invented": True},
        {"isRead": "yes"},
        {"body": {"contentType": "invented", "content": "x"}},
    ):
        with pytest.raises(ValidationError):
            MailContentImport.model_validate(
                {**base, "messages": [{"id": "m1", "folder_id": "inbox", "payload": payload}]}
            )


def test_mail_import_counts_all_records_together():
    with pytest.raises(ValidationError):
        MailContentImport.model_validate(
            {
                "mailbox_user_id": str(uuid4()),
                "mode": "content-backed",
                "expected_revision": 0,
                "messages": [
                    {"id": f"m{i}", "folder_id": "inbox", "payload": {}} for i in range(1000)
                ],
                "folders": [{"id": "inbox", "displayName": "Inbox"}],
            }
        )


def test_attachment_content_type_cannot_break_http_headers():
    from mockazure.mail.content_models import AttachmentImport

    with pytest.raises(ValidationError):
        AttachmentImport.model_validate(
            {
                "message_id": "m1",
                "id": "a1",
                "kind": "fileAttachment",
                "content_id": str(uuid4()),
                "payload": {"contentType": "text/plain\r\nX-Injected: yes"},
            }
        )


@pytest.mark.parametrize("stamp", ["2026-10-07T10:30:00.12345678Z", "20261007T10:30:00Z"])
def test_received_time_rejects_precision_or_shape_outside_graph_profile(stamp):
    from mockazure.mail.content_models import MessageImport

    with pytest.raises(ValidationError):
        MessageImport.model_validate(
            {"id": "m", "folder_id": "inbox", "payload": {"receivedDateTime": stamp}}
        )
