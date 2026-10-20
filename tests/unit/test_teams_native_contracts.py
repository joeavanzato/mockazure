from mockazure.authorization.catalog import CATALOG
from mockazure.population.catalog import get_adapter
from mockazure.population.types import Mutation


def test_native_teams_permissions_can_be_consented():
    available = {(p.name, p.kind) for p in CATALOG if p.resource == "graph"}
    assert {
        ("Chat.Create", "delegated"),
        ("Chat.Create", "application"),
        ("ChatMessage.Send", "delegated"),
        ("ChannelMessage.Send", "delegated"),
        ("ChannelMessage.ReadWrite", "delegated"),
        ("TeamMember.ReadWriteNonOwnerRole.All", "delegated"),
        ("TeamMember.ReadWriteNonOwnerRole.All", "application"),
    } <= available


def test_native_actions_use_opaque_escaped_ids_and_named_identity():
    adapter = get_adapter("teams.chatMessage")
    description = adapter.describe()
    send = next(op for op in description.operations if op.key == "send-channel")
    assert send.mode == "service" and send.identity_required and send.replay_policy == "manual"
    _, _, path = adapter.prepare(
        Mutation(
            operation="send-channel",
            logical_key="send",
            identity_ref="sender",
            payload={
                "variables": {"team": "team-1", "channel": "19:channel#one@thread.tacv2"},
                "body": {"body": {"contentType": "text", "content": "Local message"}},
            },
        )
    )
    assert "%23" in path and "#" not in path
