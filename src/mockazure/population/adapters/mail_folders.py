"""Create-only folder fixtures, including initial content-backed mailbox state."""

from uuid import UUID

from sqlalchemy import String, cast, select

from mockazure.mail.content_models import FolderImport, MailContentImport
from mockazure.mail.message_models import MailboxContentState, MailContentFolder
from mockazure.mail.messages import import_mail_content
from mockazure.mail.models import MailboxFolder
from mockazure.population.adapters.arm import json_object
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.adapters.mail import fixture


def adapters():
    def normalize(payload):
        return FolderImport.model_validate(payload).model_dump(mode="json")

    async def write(session, ctx, pending):
        for mutation, payload in pending:
            if not mutation.parent:
                raise ValueError("Mailbox parent is required")
            user = UUID(mutation.parent)
            state = await session.get(MailboxContentState, (ctx.tenant_id, user))
            await import_mail_content(
                session,
                ctx.tenant_id,
                MailContentImport(
                    mailbox_user_id=user,
                    mode="content-backed",
                    expected_revision=state.revision if state else 0,
                    folders=[FolderImport.model_validate(payload)],
                ),
                insert_only=True,
            )

    f, b = MailContentFolder, MailboxFolder
    name = (
        select(b.name)
        .where(b.tenant_id == f.tenant_id, b.user_id == f.user_id, b.id == f.id)
        .correlate(f)
        .scalar_subquery()
    )
    payload = json_object(
        id=f.id,
        displayName=name,
        parentFolderId=f.parent_id,
        isHidden=f.is_hidden,
        well_known_name=f.well_known_name,
    )
    descriptor = fixture(
        "mail.folders",
        FolderImport.model_json_schema(),
        ["id", "displayName", "parentFolderId"],
        "Append-only folders; existing mailbox and other folders remain unchanged.",
    )
    descriptor.api_path = "/v1.0/users/{user}/mailFolders"
    descriptor.operations[0].key = "append"
    return [
        PayloadAdapter(
            descriptor,
            ReadSpec(f, f.id, payload, cast(payload, String), parent=f.user_id),
            normalize,
            write,
        )
    ]
