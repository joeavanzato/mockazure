from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mockazure.db.models import Base


class ConsoleCredential(Base):
    __tablename__ = "console_credential"
    id: Mapped[int] = mapped_column(primary_key=True)
    encoded: Mapped[str] = mapped_column(Text)
    generation: Mapped[int]


class ConsoleSession(Base):
    __tablename__ = "console_sessions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    generation: Mapped[int]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked: Mapped[bool] = mapped_column(default=False)


class LoginFailure(Base):
    __tablename__ = "console_login_failures"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
