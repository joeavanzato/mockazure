"""Canonical mailbox projections, persisted statistics and atomic configuration writes."""

from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from mockazure.admin.errors import AdminError
from mockazure.identity.models import User
from mockazure.mail.authorization import require_mailbox_access
from mockazure.mail.facts import record_mail_fact
from mockazure.mail.models import Mailbox, MailboxFolder, MailSettings
from mockazure.mail.repository import resolve_mailbox
from mockazure.mail.schemas import CAS_FIELDS, smtp_address
from mockazure.providers.graph.errors import GraphError


async def mailbox_view(session, box):
    user = await session.get(User, box.user_id)
    target = await session.get(User, box.forward_user_id) if box.forward_user_id else None
    effective = None
    if box.forward_user_id:
        recipient = await session.get(Mailbox, (box.tenant_id, box.forward_user_id))
        if target and target.deleted_at is None and recipient:
            effective = recipient.primary_smtp
    elif box.forward_smtp:
        effective = box.forward_smtp.removeprefix("smtp:")
    from mockazure.mail.delegation import behalf_ids

    return {
        "GrantSendOnBehalfTo": await behalf_ids(session, box),
        "Identity": user.upn,
        "UserPrincipalName": user.upn,
        "Name": user.display_name,
        "DisplayName": user.display_name,
        "ExternalDirectoryObjectId": str(user.id),
        "ExchangeGuid": str(box.exchange_guid),
        "PrimarySmtpAddress": box.primary_smtp,
        "Alias": box.alias,
        "EmailAddresses": ["SMTP:" + box.primary_smtp] + ["smtp:" + v for v in box.aliases],
        "RecipientTypeDetails": box.mailbox_type,
        "ArchiveStatus": "Active" if box.archive_guid else "None",
        "ArchiveDatabase": f"MockAzure-Archive-{box.archive_guid}" if box.archive_guid else None,
        "ArchiveGuid": str(box.archive_guid)
        if box.archive_guid
        else "00000000-0000-0000-0000-000000000000",
        "ForwardingAddress": str(box.forward_user_id) if box.forward_user_id else None,
        "ForwardingSmtpAddress": box.forward_smtp,
        "DeliverToMailboxAndForward": box.deliver_and_forward,
        "EffectiveForwardingAddress": effective,
        "WhenChangedUTC": box.updated_at.isoformat(),
    }


async def cas_view(session, box):
    view = await mailbox_view(session, box)
    return {
        key: view[key]
        for key in (
            "Identity",
            "UserPrincipalName",
            "PrimarySmtpAddress",
            "ExternalDirectoryObjectId",
        )
    } | box.cas


async def mailbox_statistics(session, box, *, archive=False):
    if archive and box.archive_guid is None:
        raise GraphError(404, "ErrorArchiveNotFound", "Mailbox archive is not enabled")
    result = (
        await session.execute(
            select(
                func.coalesce(func.sum(MailboxFolder.item_count), 0),
                func.coalesce(func.sum(MailboxFolder.size_bytes), 0),
            ).where(
                MailboxFolder.tenant_id == box.tenant_id,
                MailboxFolder.user_id == box.user_id,
                MailboxFolder.archive.is_(archive),
            )
        )
    ).one()
    user = await session.get(User, box.user_id)
    return {
        "DisplayName": user.display_name,
        "MailboxGuid": str(box.archive_guid if archive else box.exchange_guid),
        "ItemCount": int(result[0]),
        "TotalItemSizeBytes": int(result[1]),
        "TotalItemSize": f"{result[1]} B ({result[1]} bytes)",
        "IsArchiveMailbox": archive,
    }


async def provision_mailbox(session, tenant, user_id, value, now):
    user = await session.scalar(
        select(User)
        .where(
            User.tenant_id == tenant,
            User.id == user_id,
            User.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if user is None:
        raise AdminError(404, "UserNotFound", "Mailbox user was not found")
    settings = await session.get(MailSettings, tenant)
    if settings is None or not settings.exchange_enabled:
        raise AdminError(409, "ExchangeUnavailable", "Enable the tenant Exchange entitlement first")
    box = await session.get(Mailbox, (tenant, user_id), with_for_update=True)
    from mockazure.mail.message_models import MailboxContentState

    content_backed = bool(box and await session.get(MailboxContentState, (tenant, user_id)))
    if content_backed and "folders" in value.model_fields_set:
        raise AdminError(
            409, "ContentBackedMailbox", "Use content import or reset before replacing folders"
        )
    if box is None:
        box = Mailbox(tenant_id=tenant, user_id=user_id, exchange_guid=uuid4())
        session.add(box)
    box.primary_smtp = value.primary_smtp
    box.alias = value.alias
    box.aliases = [v for v in value.aliases if v != value.primary_smtp]
    box.mailbox_type = value.mailbox_type
    if not content_backed or "archive_enabled" in value.model_fields_set:
        box.archive_guid = (box.archive_guid or uuid4()) if value.archive_enabled else None
    box.cas = {name: name != "SmtpClientAuthenticationDisabled" for name in CAS_FIELDS} | value.cas
    box.updated_at = now
    try:
        await session.flush()
    except IntegrityError:
        raise AdminError(
            409, "MailboxConflict", "Mailbox address or identity already exists"
        ) from None
    if not content_backed:
        await session.execute(
            delete(MailboxFolder).where(
                MailboxFolder.tenant_id == tenant,
                MailboxFolder.user_id == user_id,
            )
        )
        for folder in value.folders:
            session.add(MailboxFolder(tenant_id=tenant, user_id=user_id, **folder.model_dump()))
        await session.flush()
    return await mailbox_view(session, box)


async def mutate_mailbox(session, actor, mailbox, changes: dict, now) -> dict:
    from mockazure.mail.delegation import lock_delegation, replace_behalf

    await lock_delegation(session, actor.tenant_id)
    await session.refresh(mailbox, with_for_update=True)
    await require_mailbox_access(session, actor, mailbox, "Set-Mailbox")
    fields = {
        "ForwardingAddress",
        "ForwardingSmtpAddress",
        "DeliverToMailboxAndForward",
        "GrantSendOnBehalfTo",
    }
    if not isinstance(changes, dict) or not changes or changes.keys() - fields:
        raise GraphError(400, "InvalidParameter", "Unsupported mailbox configuration property")
    before = await mailbox_view(session, mailbox)
    forward_user = mailbox.forward_user_id
    forward_smtp = mailbox.forward_smtp
    deliver = mailbox.deliver_and_forward
    if "ForwardingAddress" in changes:
        identity = changes["ForwardingAddress"]
        forward_user = (
            None
            if identity is None
            else (await resolve_mailbox(session, actor.tenant_id, identity)).user_id
        )
        if forward_user == mailbox.user_id:
            raise GraphError(400, "InvalidForwardingAddress", "Mailbox cannot forward to itself")
    if "ForwardingSmtpAddress" in changes:
        value = changes["ForwardingSmtpAddress"]
        if value is None:
            forward_smtp = None
        else:
            try:
                if not isinstance(value, str):
                    raise ValueError("SMTP string required")
                forward_smtp = "smtp:" + smtp_address(
                    value[5:] if value.lower().startswith("smtp:") else value
                )
            except (ValueError, UnicodeError):
                raise GraphError(
                    400, "InvalidForwardingAddress", "Invalid SMTP forwarding address"
                ) from None
    if "DeliverToMailboxAndForward" in changes:
        deliver = changes["DeliverToMailboxAndForward"]
        if type(deliver) is not bool:
            raise GraphError(
                400, "InvalidParameter", "DeliverToMailboxAndForward requires a boolean"
            )
    if "GrantSendOnBehalfTo" in changes:
        await replace_behalf(session, mailbox, changes["GrantSendOnBehalfTo"])
    if "GrantSendOnBehalfTo" not in changes and (forward_user, forward_smtp, deliver) == (
        mailbox.forward_user_id,
        mailbox.forward_smtp,
        mailbox.deliver_and_forward,
    ):
        return before
    mailbox.forward_user_id, mailbox.forward_smtp, mailbox.deliver_and_forward = (
        forward_user,
        forward_smtp,
        deliver,
    )
    mailbox.updated_at = now
    await session.flush()
    after = await mailbox_view(session, mailbox)
    await record_mail_fact(
        session,
        actor,
        "Set-Mailbox",
        {
            "id": str(mailbox.user_id),
            "upn": after["UserPrincipalName"],
            "primarySmtpAddress": after["PrimarySmtpAddress"],
        },
        before,
        after,
        now,
    )
    return after
