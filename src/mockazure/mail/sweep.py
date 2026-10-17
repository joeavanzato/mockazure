"""Persisted Exchange Sweep configuration, separate from Graph inbox rules."""

import secrets

from sqlalchemy import select

from mockazure.identity.models import User
from mockazure.mail.authorization import require_mailbox_access
from mockazure.mail.facts import record_mail_fact
from mockazure.mail.models import MailboxFolder, MailRule
from mockazure.mail.schemas import smtp_address
from mockazure.providers.exchange.serialization import sweep_view
from mockazure.providers.graph.errors import GraphError


def invalid(message):
    raise GraphError(400, "InvalidSweepRule", message)


async def mutate_sweep(session, actor, box, name, parameters, now):
    await session.refresh(box, with_for_update=True)
    await require_mailbox_access(session, actor, box, name)
    rows = list(
        await session.scalars(
            select(MailRule)
            .where(
                MailRule.tenant_id == box.tenant_id,
                MailRule.user_id == box.user_id,
                MailRule.family == "sweep",
            )
            .order_by(MailRule.sequence)
            .execution_options(populate_existing=True)
        )
    )
    identifier = parameters.get("Identity", "").split("\\")[-1]
    matches = [r for r in rows if identifier in (r.id, r.payload["Name"])]
    row = matches[0] if len(matches) == 1 else None
    if name != "New-SweepRule" and row is None:
        raise GraphError(404, "ErrorItemNotFound", "Sweep rule was not found uniquely")
    if name == "New-SweepRule" and len(rows) >= 1000:
        raise GraphError(409, "ErrorRulesOverQuota", "Local Sweep rule limit reached")
    before = sweep_view(box, row) if row else {}
    if name == "Remove-SweepRule":
        rows.remove(row)
        await session.delete(row)
        for position, other in enumerate(rows, 1):
            other.sequence = position
        after = {}
    else:
        changes = {k: v for k, v in parameters.items() if k not in {"Mailbox", "Identity"}}
        value = (
            row.payload
            if row
            else {
                "Enabled": True,
                "SourceFolder": "inbox",
                "KeepLatest": False,
                "KeepForDays": 0,
                "ExceptIfFlagged": False,
                "ExceptIfPinned": False,
            }
        ) | changes
        for key in ("Name", "Provider", "Sender", "SourceFolder", "DestinationFolder"):
            if (
                not isinstance(value.get(key), str)
                or not 1 <= len(value[key]) <= 320
                or "\0" in value[key]
            ):
                invalid("Sweep rule requires bounded name, provider, sender and folders")
        if value["Provider"] != "Exchange16":
            invalid("Only the Exchange16 Sweep provider is supported")
        try:
            value["Sender"] = smtp_address(value["Sender"])
        except ValueError:
            invalid("Invalid Sweep sender")
        for key in ("Enabled", "KeepLatest", "ExceptIfFlagged", "ExceptIfPinned"):
            if type(value[key]) is not bool:
                invalid("Sweep flags require booleans")
        if type(value["KeepForDays"]) is not int or not 0 <= value["KeepForDays"] <= 3650:
            invalid("Invalid Sweep retention interval")
        for key in ("SourceFolder", "DestinationFolder"):
            if await session.get(MailboxFolder, (box.tenant_id, box.user_id, value[key])) is None:
                invalid("Sweep folder was not found")
        if value["SourceFolder"] == value["DestinationFolder"]:
            invalid("Sweep source and destination must differ")
        if row is None:
            row = MailRule(
                tenant_id=box.tenant_id,
                user_id=box.user_id,
                id=secrets.token_urlsafe(18),
                family="sweep",
                sequence=len(rows) + 1,
                hidden=False,
                read_only=False,
                payload=value,
            )
            session.add(row)
        else:
            row.payload = value
        after = sweep_view(box, row)
    user = await session.get(User, box.user_id)
    await record_mail_fact(
        session,
        actor,
        name,
        {
            "id": str(box.user_id),
            "upn": user.upn,
            "primarySmtpAddress": box.primary_smtp,
        },
        before,
        after,
        now,
    )
    await session.flush()
    return [after] if name == "New-SweepRule" else []
