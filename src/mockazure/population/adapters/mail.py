"""Mailbox and Graph mail/calendar read projections."""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import Field
from sqlalchemy import String, cast

from mockazure.mail.content_models import MailContentImport, MessageImport
from mockazure.mail.mailboxes import provision_mailbox
from mockazure.mail.message_models import MailboxContentState, MailMessage
from mockazure.mail.messages import import_mail_content
from mockazure.mail.models import Mailbox, MailSettings
from mockazure.mail.schemas import MailboxInput, MailSettingsInput
from mockazure.population.adapters.arm import json_object
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.adapters.calendar_history import adapter as calendar_history_adapter
from mockazure.population.adapters.connected_mail import (
    ConnectedMessage,
    matches_document,
    materialize,
)
from mockazure.population.types import FamilyDescriptor, Input, OperationDescriptor


class BoxFixture(Input):
    id: UUID
    primary_smtp: str
    alias: str = Field(min_length=1, max_length=128)
    mailbox_type: str = "UserMailbox"


def fixture(key, schema, fields, limitation):
    return FamilyDescriptor(
        key=key,
        service="Exchange and mail",
        api_path="/v1.0/users/{user}/messages",
        display_fields=fields,
        operations=[
            OperationDescriptor(
                key="provision",
                mode="fixture",
                method="POST",
                path_template="/_mock/tenants/{tenant}/resources/" + key + "/apply",
                api_version="local-fixture-v1",
                input_schema=schema,
            )
        ],
        contract_refs=["docs/mail-content.md"],
        limitations=[limitation],
    )


class MessageAdapter(PayloadAdapter):
    def normalize(self, mutation):
        if mutation.operation == "append-connected":
            value = ConnectedMessage.model_validate(mutation.payload)
            return value.message.model_dump(mode="json", exclude_unset=True)
        return super().normalize(mutation)

    async def matches_mutation(self, session, ctx, old, mutation, payload):
        if not self.matches(old.properties, payload):
            return False
        if mutation.operation == "append-connected":
            return await matches_document(
                session,
                ctx.tenant_id,
                old.properties["attachments"],
                ConnectedMessage.model_validate(mutation.payload),
            )
        return True

    def matches(self, existing, desired):
        return all(existing.get(k) == v for k, v in desired.items() if k != "payload") and all(
            existing.get("payload", {}).get(k) == v for k, v in desired["payload"].items()
        )


def message_adapter():
    def normalize(payload):
        return MessageImport.model_validate(payload).model_dump(mode="json", exclude_unset=True)

    async def write(session, ctx, pending):
        for mutation, payload in pending:
            if not mutation.parent:
                raise ValueError("Mailbox parent is required")
            user = UUID(mutation.parent)
            state = await session.get(MailboxContentState, (ctx.tenant_id, user))
            message, attachments = MessageImport.model_validate(payload), []
            if mutation.operation == "append-connected":
                message, attachment = await materialize(
                    session, ctx, ConnectedMessage.model_validate(mutation.payload)
                )
                attachments = [attachment]
            await import_mail_content(
                session,
                ctx.tenant_id,
                MailContentImport(
                    mailbox_user_id=user,
                    mode="content-backed",
                    expected_revision=state.revision if state else 0,
                    messages=[message],
                    attachments=attachments,
                ),
                insert_only=True,
            )

    m = MailMessage
    descriptor = fixture(
        "mail.messages",
        MessageImport.model_json_schema(),
        ["id", "folder_id", "subject"],
        "Append-only historical messages. Existing folders required; pending mail workflows "
        "remain active.",
    )
    descriptor.operations[0].key = "append"
    descriptor.operations.append(
        OperationDescriptor(
            key="append-connected",
            mode="fixture",
            method="POST",
            path_template="/_mock/tenants/{tenant}/resources/mail.messages/apply",
            api_version="local-fixture-v1",
            input_schema=ConnectedMessage.model_json_schema(),
        )
    )
    payload = json_object(
        id=m.id,
        folder_id=m.folder_id,
        immutable_id=m.immutable_id,
        payload=m.payload,
        mime_content_id=m.mime_content_id,
        size_bytes=m.size_bytes,
        attachments=m.attachments,
        subject=m.payload["subject"],
    )
    return MessageAdapter(
        descriptor, ReadSpec(m, m.id, payload, m.revision, parent=m.user_id), normalize, write
    )


def adapters():
    b = Mailbox

    def normalize_box(payload):
        value = BoxFixture.model_validate(payload)
        checked = MailboxInput.model_validate(value.model_dump(exclude={"id"}))
        return {
            "id": str(value.id),
            **checked.model_dump(include={"primary_smtp", "alias", "mailbox_type"}),
        }

    async def write_box(session, ctx, pending):
        for _, payload in pending:
            await provision_mailbox(
                session,
                ctx.tenant_id,
                UUID(payload["id"]),
                MailboxInput.model_validate({k: v for k, v in payload.items() if k != "id"}),
                datetime.now(UTC),
            )

    def normalize_settings(payload):
        return {
            "id": str(UUID(payload["id"])),
            **MailSettingsInput.model_validate(
                {k: v for k, v in payload.items() if k != "id"}
            ).model_dump(),
        }

    async def write_settings(session, ctx, pending):
        for _, payload in pending:
            if payload["id"] != str(ctx.tenant_id):
                raise ValueError("Settings belong to another tenant")
            row = await session.get(MailSettings, ctx.tenant_id, with_for_update=True)
            if row is None:
                row = MailSettings(tenant_id=ctx.tenant_id)
                session.add(row)
            row.exchange_enabled, row.audit_tier = (
                payload["exchange_enabled"],
                payload["audit_tier"],
            )

    s = MailSettings
    settings_payload = json_object(
        id=s.tenant_id, exchange_enabled=s.exchange_enabled, audit_tier=s.audit_tier
    )
    return [
        PayloadAdapter(
            fixture(
                "mail.settings",
                MailSettingsInput.model_json_schema(),
                ["id", "exchange_enabled", "audit_tier"],
                "Explicit local Exchange entitlement and audit tier; replacement affects the "
                "tenant mail surface.",
            ),
            ReadSpec(s, s.tenant_id, settings_payload, cast(settings_payload, String)),
            normalize_settings,
            write_settings,
            replace_allowed=True,
        ),
        PayloadAdapter(
            fixture(
                "mail.mailboxes",
                BoxFixture.model_json_schema(),
                ["id", "primary_smtp", "alias", "mailbox_type"],
                "Create-only mailbox provisioning; enable Exchange first. Folder/content "
                "initialization is a separate operation.",
            ),
            ReadSpec(
                b,
                b.user_id,
                json_object(
                    id=b.user_id,
                    primary_smtp=b.primary_smtp,
                    alias=b.alias,
                    mailbox_type=b.mailbox_type,
                ),
                b.updated_at,
            ),
            normalize_box,
            write_box,
        ),
        message_adapter(),
        calendar_history_adapter(),
    ]
