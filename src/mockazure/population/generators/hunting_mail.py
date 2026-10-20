"""Explicit fixture mail telemetry with separate message namespaces."""


def row(g, table, i):
    i %= max(1, g.parameters.get("messageCopies", 32) // 2)
    thread, reply = divmod(i, 2)
    sender, recipient = (thread + reply) % g.users, (thread + 1 - reply) % g.users
    sender, recipient = g.person(sender), g.person(recipient)
    common = {"NetworkMessageId": g.id("network-message", i), "ReportId": g.id("hunting-report", i)}
    internet = "<" + g.id("internet-message", i) + "@" + g.domain + ">"
    url = "https://documents.example.test/project/" + str(thread % g.projects + 1)
    if table == "EmailUrlInfo":
        return common | {"Url": url, "UrlDomain": "documents.example.test", "UrlLocation": "Body"}
    if table == "UrlClickEvents":
        return common | {
            "Url": url,
            "AccountUpn": recipient["userPrincipalName"],
            "Workload": "Email",
            "ActionType": "ClickAllowed",
            "IsClickedThrough": False,
            "IPAddress": "192.0.2.42",
        }
    if table == "EmailPostDeliveryEvents":
        return common | {
            "InternetMessageId": internet,
            "RecipientEmailAddress": recipient["userPrincipalName"],
            "Action": "Move to deleted items",
            "ActionType": "Manual remediation",
            "ActionTrigger": "AdminAction",
            "ActionResult": "Success",
            "DeliveryLocation": "Deleted items",
        }
    common.update(
        SenderFromAddress=sender["userPrincipalName"],
        SenderDisplayName=sender["displayName"],
        SenderObjectId=sender["id"],
        RecipientEmailAddress=recipient["userPrincipalName"],
        RecipientObjectId=recipient["id"],
    )
    if table == "EmailAttachmentInfo":
        doc = g.rendered(thread % g.projects, reply + 1)
        return common | {
            "FileName": doc.filename,
            "FileType": "docx",
            "FileExtension": "docx",
            "SHA256": doc.sha256,
            "FileSize": doc.length,
        }
    return common | {
        "InternetMessageId": internet,
        "Subject": ("Re: " if reply else "") + f"Project {thread % g.projects + 1} review",
        "EmailDirection": "Intra-org",
        "DeliveryAction": "Delivered",
        "DeliveryLocation": "Inbox/folder",
        "AttachmentCount": 1,
        "UrlCount": 1,
        "SenderFromDomain": g.domain,
    }
