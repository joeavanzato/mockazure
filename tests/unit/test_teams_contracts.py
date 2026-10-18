import pytest

from mockazure.workloads.teams import validate_team_record


def test_teams_schema_preserves_message_content_and_rejects_invented_fields():
    message = {
        "id": "1700#opaque",
        "body": {"contentType": "html", "content": "<p>Evidence</p>"},
        "importance": "normal",
        "locale": "en-us",
        "messageType": "message",
        "createdDateTime": "2026-10-07T00:00:00Z",
        "lastModifiedDateTime": "2026-10-07T00:00:00Z",
        "attachments": [
            {
                "id": "a",
                "contentType": "reference",
                "contentUrl": "https://external.invalid/evidence",
            }
        ],
    }
    validate_team_record("chatMessage", message)
    with pytest.raises(ValueError):
        validate_team_record("chatMessage", {**message, "invented": True})
    with pytest.raises(ValueError):
        validate_team_record("chatMessage", {**message, "replies": []})
    with pytest.raises(ValueError):
        validate_team_record("call", {"id": "c"})


def test_team_member_schema_and_rsc_names_are_not_graph_application_roles():
    from mockazure.authorization.catalog import CATALOG
    from mockazure.workloads.team_access import RSC_PERMISSIONS

    validate_team_record(
        "conversationMember",
        {
            "id": "member#one",
            "@odata.type": "#microsoft.graph.aadUserConversationMember",
            "userId": "00000000-0000-0000-0000-000000000001",
            "roles": ["owner"],
        },
    )
    assert "ChannelMessage.Read.Group" in RSC_PERMISSIONS
    assert "ChatMessage.Read.Chat" in RSC_PERMISSIONS
    assert not {row.name for row in CATALOG} & RSC_PERMISSIONS
