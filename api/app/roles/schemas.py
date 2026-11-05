"""Pydantic response schema for role-request routes."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.roles.models import RoleRequestStatus


class RoleRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    status: RoleRequestStatus
    requested_at: datetime
    decided_at: datetime | None
    decided_by: uuid.UUID | None
