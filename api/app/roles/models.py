"""Role-request ORM model.

A `RoleRequest` records a user's request to be upgraded from student to
professor. An admin later approves it (promoting the requester's role to
professor) or denies it (leaving the role unchanged); either decision is
final and recorded via `decided_at`/`decided_by` (see `app/roles/service.py`).

A partial unique index enforces at most one *pending* request per user - a
user whose prior request was denied (or approved) can submit again, but can
never have two pending requests at once.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RoleRequestStatus(enum.StrEnum):
    pending = "pending"
    approved = "approved"
    denied = "denied"


class RoleRequest(Base):
    __tablename__ = "role_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_role_requests_user_id_users"),
        nullable=False,
    )
    status: Mapped[RoleRequestStatus] = mapped_column(
        Enum(RoleRequestStatus, name="role_request_status"),
        nullable=False,
        default=RoleRequestStatus.pending,
        server_default=RoleRequestStatus.pending.value,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", name="fk_role_requests_decided_by_users"),
        nullable=True,
    )

    __table_args__ = (
        # Guards data integrity: a user can never have two
        # requests awaiting a decision at once. Approved/denied requests are
        # excluded from the index, so a user may submit again after a denial.
        Index(
            "uq_role_requests_one_pending_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )
