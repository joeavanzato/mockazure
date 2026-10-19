"""Large exported notification state has an explicit local bound."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mockazure.admin.errors import AdminError
from mockazure.notifications.lifecycle import export_notifications


async def test_notification_export_overflow_is_explicit():
    row = SimpleNamespace(
        client_id=uuid4(),
        payload={"resource": "users/example/messages", "changeType": "created"},
        expires_at=datetime.now(UTC),
    )
    session = SimpleNamespace(scalars=AsyncMock(return_value=[row] * 1001))
    with pytest.raises(AdminError) as error:
        await export_notifications(session, uuid4())
    assert error.value.status == 413
    assert error.value.code == "NotificationStateLimit"
