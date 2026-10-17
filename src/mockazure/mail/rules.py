"""Transactional rule ordering and immutable page versions."""

import secrets

from sqlalchemy import or_, select, tuple_, update

from mockazure.directory.mutations import AdminActor
from mockazure.mail.authorization import EXCHANGE, require_mailbox_access
from mockazure.mail.contracts import validate_mail_payload
from mockazure.mail.facts import record_mail_fact
from mockazure.mail.models import MailboxFolder, MailRule, MailRuleVersion
from mockazure.mail.rule_projection import rule_view, selected
from mockazure.mail.schemas import smtp_address
from mockazure.providers.graph.errors import GraphError

MAX_RULES = 1000


def invalid(message):
    raise GraphError(400, "ErrorInvalidRule", message)


async def validate_rule(session, mailbox, body, *, partial):
    try:
        value = validate_mail_payload("messageRule", body, partial=partial)
    except ValueError:
        invalid("Invalid messageRule payload")
    actions = value.get("actions", {})
    if actions.get("delete") and (actions.get("permanentDelete") or actions.get("moveToFolder")):
        invalid("Delete and move/permanent-delete actions cannot be combined")
    if actions.get("permanentDelete") and any(
        actions.get(k)
        for k in (
            "moveToFolder",
            "copyToFolder",
            "forwardTo",
            "redirectTo",
            "forwardAsAttachmentTo",
        )
    ):
        invalid("Permanent delete cannot be combined with delivery actions")
    for key in ("moveToFolder", "copyToFolder"):
        if key in actions:
            folder = await session.get(
                MailboxFolder, (mailbox.tenant_id, mailbox.user_id, actions[key])
            )
            if folder is None or folder.archive:
                invalid("Rule destination must be an existing primary mailbox folder")
    for group in (actions, value.get("conditions") or {}, value.get("exceptions") or {}):
        for key in (
            "forwardTo",
            "redirectTo",
            "forwardAsAttachmentTo",
            "fromAddresses",
            "sentToAddresses",
        ):
            for recipient in group.get(key) or []:
                try:
                    address = recipient["emailAddress"]["address"]
                    smtp_address(address)
                except (KeyError, TypeError, ValueError, UnicodeError):
                    invalid("Rule recipient requires a valid email address")
        size = group.get("withinSizeRange")
        if size:
            minimum, maximum = size.get("minimumSize", 0), size.get("maximumSize", 2**31 - 1)
            if minimum is None or maximum is None or minimum < 0 or maximum < minimum:
                invalid("Invalid rule size range")
    return value


async def mutate_rule(session, actor, mailbox, operation: str, rule_id, body: dict, now):
    return await _mutate_rule(session, actor, mailbox, operation, rule_id, body, now)


async def _mutate_rule(
    session, actor, mailbox, operation, rule_id, body, now, *, hidden=False, read_only=False
):
    if operation not in {"create", "update", "delete"}:
        invalid("Unsupported rule operation")
    await session.refresh(mailbox, with_for_update=True)
    admin = isinstance(actor, AdminActor)
    exchange = not admin and actor.audience == EXCHANGE
    if not admin:
        permission = (
            {"create": "New-InboxRule", "update": "Set-InboxRule", "delete": "Remove-InboxRule"}[
                operation
            ]
            if exchange
            else "rules." + operation
        )
        await require_mailbox_access(session, actor, mailbox, permission)
    rows = list(
        await session.scalars(
            select(MailRule)
            .where(
                MailRule.tenant_id == mailbox.tenant_id,
                MailRule.user_id == mailbox.user_id,
                MailRule.family == "inbox",
            )
            .order_by(MailRule.sequence, MailRule.id)
        )
    )
    row = next((r for r in rows if r.id == rule_id), None)
    if operation != "create":
        if row is None or (row.hidden and not (admin or exchange)):
            raise GraphError(404, "ErrorItemNotFound", "Rule was not found")
        if row.read_only:
            raise GraphError(403, "ErrorAccessDenied", "Rule is read-only")
    if operation == "create" and len(rows) >= MAX_RULES:
        raise GraphError(409, "ErrorRulesOverQuota", "Local mailbox rule limit reached")
    before_versions = {r.id: rule_view(r) for r in rows}
    before = before_versions.get(rule_id, {})
    value = (
        {}
        if operation == "delete"
        else await validate_rule(session, mailbox, body, partial=operation == "update")
    )
    if operation == "create":
        position = max(0, min(value.pop("sequence") - 1, len(rows)))
        row = MailRule(
            tenant_id=mailbox.tenant_id,
            user_id=mailbox.user_id,
            id=secrets.token_urlsafe(18),
            family="inbox",
            sequence=1,
            hidden=hidden,
            read_only=read_only,
            payload={"isEnabled": True, "conditions": {}, "exceptions": {}, **value},
        )
        rows.insert(position, row)
        session.add(row)
    elif operation == "update":
        if "sequence" in value:
            rows.remove(row)
            rows.insert(max(0, min(value.pop("sequence") - 1, len(rows))), row)
        row.payload = row.payload | value
    else:
        rows.remove(row)
        await session.delete(row)
    mailbox.rule_revision += 1
    revision = mailbox.rule_revision
    for index, current in enumerate(rows, start=1):
        current.sequence = index
    await session.flush()
    after_versions = {r.id: rule_view(r) for r in rows}
    changed = {
        key
        for key in before_versions.keys() | after_versions.keys()
        if before_versions.get(key) != after_versions.get(key)
    }
    if changed:
        await session.execute(
            update(MailRuleVersion)
            .where(
                MailRuleVersion.tenant_id == mailbox.tenant_id,
                MailRuleVersion.user_id == mailbox.user_id,
                MailRuleVersion.id.in_(changed),
                MailRuleVersion.valid_to.is_(None),
            )
            .values(valid_to=revision)
        )
    for current in rows:
        if current.id in changed:
            session.add(
                MailRuleVersion(
                    tenant_id=mailbox.tenant_id,
                    user_id=mailbox.user_id,
                    id=current.id,
                    valid_from=revision,
                    valid_to=None,
                    sequence=current.sequence,
                    family="inbox",
                    hidden=current.hidden,
                    payload=after_versions[current.id],
                )
            )
    after = after_versions.get(row.id, {})
    if not admin:
        from mockazure.identity.models import User

        user = await session.get(User, mailbox.user_id)
        await record_mail_fact(
            session,
            actor,
            {
                "create": "New-InboxRule",
                "update": "Set-InboxRule",
                "delete": "Remove-InboxRule",
            }[operation],
            {
                "id": str(mailbox.user_id),
                "upn": user.upn,
                "primarySmtpAddress": mailbox.primary_smtp,
            },
            before,
            after,
            now,
        )
    await session.flush()
    return after if operation != "delete" else None


async def rule_page(session, actor, mailbox, query, cursor):
    await require_mailbox_access(session, actor, mailbox, "rules.list")
    revision = cursor.upper_revision if cursor else mailbox.rule_revision
    query = cursor.query if cursor else query
    limit = min(query.top or 100, 100)
    conditions = [
        MailRuleVersion.tenant_id == mailbox.tenant_id,
        MailRuleVersion.user_id == mailbox.user_id,
        MailRuleVersion.family == "inbox",
        MailRuleVersion.hidden.is_(False),
        MailRuleVersion.valid_from <= revision,
        or_(MailRuleVersion.valid_to.is_(None), MailRuleVersion.valid_to > revision),
    ]
    if cursor and cursor.last_key:
        conditions.append(tuple_(MailRuleVersion.sequence, MailRuleVersion.id) > cursor.last_key)
    rows = list(
        await session.scalars(
            select(MailRuleVersion)
            .where(*conditions)
            .order_by(MailRuleVersion.sequence, MailRuleVersion.id)
            .limit(limit + 1)
        )
    )
    visible = rows[:limit]
    return {
        "value": [selected(row.payload, query.select) for row in visible],
        "revision": revision,
        "last_key": (visible[-1].sequence, visible[-1].id) if len(rows) > limit else None,
    }
