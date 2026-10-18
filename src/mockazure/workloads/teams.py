"""Pinned Teams structural contracts and explicit conversation relationships."""

from mockazure.providers.graph.schema_reference import validate_payload

KINDS = frozenset(
    {
        "team",
        "channel",
        "chat",
        "chatMessage",
        "conversationMember",
        "chatMessageHostedContent",
        "filesFolderReference",
    }
)
EDGES = frozenset(
    {
        ("team", "channels", "channel"),
        ("channel", "filesFolder", "filesFolderReference"),
        ("team", "members", "conversationMember"),
        ("channel", "members", "conversationMember"),
        ("chat", "members", "conversationMember"),
        ("channel", "messages", "chatMessage"),
        ("chat", "messages", "chatMessage"),
        ("chatMessage", "replies", "chatMessage"),
        ("chatMessage", "hostedContents", "chatMessageHostedContent"),
    }
)


def validate_team_record(kind, payload):
    if kind not in KINDS:
        raise ValueError("Unsupported Teams evidence kind")
    if kind == "filesFolderReference":
        if set(payload) != {"id", "driveId", "itemId"} or any(
            not isinstance(value, str)
            or not 1 <= len(value.encode("utf-8")) <= 512
            or any(ord(c) < 32 for c in value)
            for value in payload.values()
        ):
            raise ValueError("File folder references require bounded canonical drive/item IDs")
        return
    validate_payload("v1.0", kind, payload)
    if kind == "chatMessageHostedContent" and "contentBytes" in payload:
        raise ValueError("Use finalized local binary content for hosted content bytes")


def validate_tree(keys, edges, scope):
    roots = [key for key in keys if key[0] in {"team", "chat"}]
    if not keys:
        return
    if len(roots) != 1 or roots[0][1] != scope:
        raise ValueError("Teams scope requires exactly one canonical team or chat")
    parents, file_parents = {}, set()
    for source, relation, target in edges:
        if relation == "filesFolder":
            if source in file_parents:
                raise ValueError("Channel has multiple files folders")
            file_parents.add(source)
        if target in parents:
            raise ValueError("Conversation evidence requires a single parent")
        parents[target] = (source, relation)
    for key in keys - set(roots):
        seen, current = set(), key
        while current not in roots:
            if current in seen or current not in parents:
                raise ValueError("Conversation evidence is orphaned or cyclic")
            seen.add(current)
            current = parents[current][0]
        if key[0] == "chatMessage" and parents[key][1] == "replies":
            parent = parents[key][0]
            if parent not in parents or parents[parent][0][0] != "channel":
                raise ValueError("Replies belong to channel root messages only")
