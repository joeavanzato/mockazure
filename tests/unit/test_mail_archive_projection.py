from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mockazure.mail.mailboxes import mailbox_view


@pytest.mark.asyncio
async def test_archive_database_is_stable_and_absent_when_disabled():
    user = SimpleNamespace(id=uuid4(), upn="test@example.test", display_name="Test")
    box = SimpleNamespace(
        user_id=user.id,
        forward_user_id=None,
        forward_smtp=None,
        exchange_guid=uuid4(),
        primary_smtp=user.upn,
        alias="test",
        aliases=[],
        mailbox_type="UserMailbox",
        archive_guid=uuid4(),
        deliver_and_forward=False,
        updated_at=datetime.now(UTC),
        tenant_id=uuid4(),
    )
    session = SimpleNamespace(get=AsyncMock(return_value=user))
    first = await mailbox_view(session, box)
    assert first["ArchiveDatabase"]
    assert first["ArchiveDatabase"] == (await mailbox_view(session, box))["ArchiveDatabase"]
    box.archive_guid = None
    assert (await mailbox_view(session, box))["ArchiveDatabase"] is None
