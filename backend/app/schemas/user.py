"""User and authentication payloads."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.constants import Honorific, Role
from app.core.security import MAX_PASSWORD_BYTES
from app.schemas.common import ORMModel
from app.core.validators import Phone

# bcrypt ignores anything past 72 bytes, so the limit is enforced at the edge
# rather than letting a long password be silently truncated.
PasswordStr = Field(min_length=8, max_length=MAX_PASSWORD_BYTES)


def _validate_password_bytes(value: str) -> str:
    if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes.")
    return value


# ------------------------------------------------------------------- auth
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_change_password: bool
    user: UserOut


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=200)
    new_password: str = PasswordStr

    _check = field_validator("new_password")(_validate_password_bytes)


# ------------------------------------------------------------------ users
class TeamOut(ORMModel):
    id: uuid.UUID
    name: str
    code: str


class DepartmentOut(ORMModel):
    id: uuid.UUID
    name: str
    code: str


class UserOut(ORMModel):
    id: uuid.UUID
    name: str
    email: str
    phone: str | None = None
    role: str
    title: str | None = None
    #: "SIR" / "MAAM" / null. How the company addresses this person; set by an
    #: administrator, never inferred.
    honorific: str | None = None
    manager_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    heads_department_id: uuid.UUID | None = None
    is_active: bool
    must_change_password: bool
    plain_password: str | None = "ChangeMe@123"
    deactivated_at: datetime | None = None
    created_at: datetime


class UserDetail(UserOut):
    """A user plus the context the Team page needs, resolved server-side."""

    manager_name: str | None = None
    team_name: str | None = None
    heads_department_name: str | None = None
    direct_report_count: int = 0
    # Whether the CALLER may act on this user. The frontend hides buttons
    # from this rather than re-deriving the rule.
    can_act_on: bool = False


class UserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = PasswordStr
    role: Role
    phone: Phone = Field(default=None)
    title: str | None = Field(default=None, max_length=80)
    honorific: Honorific | None = None
    manager_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None

    _check = field_validator("password")(_validate_password_bytes)


class UserUpdate(BaseModel):
    """Every field optional: a PATCH changes only what it names.

    `role` and `manager_id` are here but are gated hard in the router - each
    re-runs the authority checks against the pre-change target.
    """

    name: str | None = Field(default=None, min_length=2, max_length=120)
    email: EmailStr | None = None
    phone: Phone = Field(default=None)
    title: str | None = Field(default=None, max_length=80)
    honorific: Honorific | None = None
    role: Role | None = None
    manager_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    heads_department_id: uuid.UUID | None = None
    is_active: bool | None = None


class SelfUpdate(BaseModel):
    """The strictly smaller set a user may change about themselves.

    Role, manager and active status are absent by design - including for a
    SUPER_ADMIN, so the installation cannot lock itself out.
    """

    name: str | None = Field(default=None, min_length=2, max_length=120)
    phone: Phone = Field(default=None)


class ResetPasswordRequest(BaseModel):
    """Set somebody's password.

    `new_password` is optional: omit it and the server generates a strong one
    and returns it, which is the only honest answer to "show me their
    password". Stored passwords are one-way hashes and cannot be recovered.
    """

    new_password: (
        Annotated[str, Field(min_length=8, max_length=MAX_PASSWORD_BYTES)] | None
    ) = None
    #: Whether they must replace it at next sign-in. On by default - a
    #: password two people know is not a password. Turn it off when the point
    #: is to hand somebody a working login they will keep using.
    must_change: bool = True

    @field_validator("new_password")
    @classmethod
    def _check(cls, value: str | None) -> str | None:
        return None if value is None else _validate_password_bytes(value)


class PasswordSetOut(BaseModel):
    """The outcome of setting a password. Never the password itself.

    Nothing here, and nothing anywhere else in the API, can return a
    password: they are stored as one-way hashes, and the plaintext must not
    travel back out even once. The administrator types the new password (or
    has the browser generate one), so they already have it - the server has
    no reason to repeat it.
    """

    message: str
    must_change_password: bool


class OrgNode(ORMModel):
    id: uuid.UUID
    name: str
    role: str
    title: str | None = None
    team_name: str | None = None
    is_active: bool
    reports: list[OrgNode] = []


class AssignableRoles(BaseModel):
    roles: list[str]


UserDetail.model_rebuild()
OrgNode.model_rebuild()
TokenResponse.model_rebuild()
