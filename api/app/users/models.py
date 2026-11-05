"""User and magic-link-request ORM models.

`User` is the single identity record shared by both auth paths (CAS and
magic-link) - `auth_method` records which path created the row.  New users
always default to the `student` role; role changes (professor
approval) are applied later by an admin, never by re-reading a cached claim
from the session cookie.

`MagicLinkRequest` exists purely to make magic-link tokens single-use:
`itsdangerous` proves a token's integrity and recency but has no concept of
"already redeemed", so `used_at` is the explicit guard `verify_magic_link`
checks and sets.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Role(enum.StrEnum):
    student = "student"
    professor = "professor"
    admin = "admin"


class AuthMethod(enum.StrEnum):
    cas = "cas"
    magic_link = "magic_link"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Nullable: magic-link-only users (no CAS ticket) never receive a
    # computing_id; CAS-authenticated users always have one.
    computing_id: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="role"), nullable=False, server_default=Role.student.value
    )
    auth_method: Mapped[AuthMethod] = mapped_column(
        Enum(AuthMethod, name="auth_method"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MagicLinkRequest(Base):
    __tablename__ = "magic_link_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
