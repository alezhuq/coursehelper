"""Pydantic request/response models for the auth routes."""

from pydantic import BaseModel, EmailStr


class MagicLinkRequestBody(BaseModel):
    email: EmailStr


class MagicLinkVerifyBody(BaseModel):
    token: str


class CurrentUserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    auth_method: str
