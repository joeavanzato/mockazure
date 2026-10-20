from datetime import timedelta

from mockazure.population.types import Mutation


def item(g, kind, i):
    if kind == "mailboxes":
        if g.count > g.users:
            raise ValueError("Mailbox count exceeds users")
        user = g.person(i)
        return Mutation(
            operation="provision",
            logical_key=f"business.mailboxes:{i}",
            payload={
                "id": user["id"],
                "primary_smtp": user["userPrincipalName"],
                "alias": user["mailNickname"],
            },
        )
    if kind == "mail-folders":
        if g.count > g.users * 3:
            raise ValueError("Folder count exceeds mailbox fixture inventory")
        folder = ("inbox", "sentitems", "drafts")[i % 3]
        return Mutation(
            operation="append",
            logical_key=f"business.mail-folders:{i}",
            parent=g.person(i // 3)["id"],
            payload={"id": folder, "displayName": folder.title(), "well_known_name": folder},
        )
    if kind == "events":
        meeting, copy = divmod(i, 2)
        if g.users == 1:
            meeting, copy = i, 0
        organizer, attendee = meeting % g.users, (meeting + 1) % g.users
        start = (g.reference - timedelta(days=g.days)).replace(hour=10, minute=0, second=0)
        date = start.date().isoformat()
        exception_day = (start + timedelta(days=1)).date().isoformat()
        payload = {
            "subject": f"Project {meeting % g.projects + 1} review",
            "body": {
                "contentType": "text",
                "content": "Review the project document and open actions.",
            },
            "start": {"dateTime": start.replace(tzinfo=None).isoformat(), "timeZone": "UTC"},
            "end": {
                "dateTime": (start + timedelta(minutes=30)).replace(tzinfo=None).isoformat(),
                "timeZone": "UTC",
            },
            "isOrganizer": copy == 0,
            "organizer": g.address(organizer),
            "iCalUId": g.id("meeting", meeting),
            "type": "seriesMaster",
            "attendees": [
                {
                    "type": "required",
                    **g.address(attendee),
                    "status": {"response": "accepted", "time": g.time()},
                }
            ],
            "responseStatus": {
                "response": "organizer" if copy == 0 else "accepted",
                "time": g.time(),
            },
            "recurrence": {
                "pattern": {"type": "daily", "interval": 1},
                "range": {
                    "type": "numbered",
                    "startDate": date,
                    "numberOfOccurrences": max(2, min(g.days, 30)),
                    "recurrenceTimeZone": "UTC",
                },
            },
            "createdDateTime": g.time(),
            "lastModifiedDateTime": g.time(),
        }
        if g.users == 1:
            payload["attendees"] = []
        return Mutation(
            operation="import",
            logical_key=f"business.events:{i}",
            parent=g.person(organizer if copy == 0 else attendee)["id"],
            payload={
                "id": g.id("event-copy", i),
                "meeting_id": g.id("meeting", meeting),
                "organizer_id": g.person(organizer)["id"],
                "payload": payload,
                "exceptions": [
                    {
                        "day": exception_day,
                        "overrides": {"subject": payload["subject"] + " follow-up"},
                        "updated_at": g.time(),
                        "deleted": False,
                    }
                ],
            },
        )
    message, copy = divmod(i, 2)
    thread, reply = divmod(message, 2)
    sender, recipient = (thread + reply) % g.users, (thread + 1 - reply) % g.users
    internet_id = "<" + g.id("internet-message", message) + "@" + g.domain + ">"
    headers = [{"name": "Message-ID", "value": internet_id}]
    if reply:
        previous = "<" + g.id("internet-message", message - 1) + "@" + g.domain + ">"
        headers += [
            {"name": "In-Reply-To", "value": previous},
            {"name": "References", "value": previous},
        ]
    timestamp = g.time(message)
    message_input = {
        "id": g.id("message-copy", i),
        "immutable_id": g.id("message-immutable", i),
        "folder_id": "sentitems" if copy == 0 else "inbox",
        "payload": {
            "subject": ("Re: " if reply else "") + f"Project {thread % g.projects + 1} review",
            "conversationId": g.id("conversation", thread),
            "internetMessageId": internet_id,
            "internetMessageHeaders": headers,
            "from": g.address(sender),
            "sender": g.address(sender),
            "toRecipients": [g.address(recipient)],
            "sentDateTime": timestamp,
            "receivedDateTime": timestamp,
            "createdDateTime": timestamp,
            "lastModifiedDateTime": timestamp,
            "isRead": copy == 0,
            "isDraft": False,
            "body": {
                "contentType": "text",
                "content": "Please review the attached project document.",
            },
        },
    }
    return Mutation(
        operation="append-connected",
        logical_key=f"business.messages:{i}",
        parent=g.person(sender if copy == 0 else recipient)["id"],
        payload={"message": message_input, "document": g.document(thread % g.projects, reply + 1)},
    )
