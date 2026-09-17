"""User and authentication payloads."""
from __future__ import annotations

import uuid
from datetime import datetime
from pydantic import AliasChoices, BaseModel, EmailStr, Field, field_validator, model_validator

from app.core.constants import Honorific, Role
from app.core.security import MAX_PASSWORD_BYTES
from app.schemas.common import ORMModel
from app.core.validators import Phone



def _validate_password_bytes(value: str) -> str:
    if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes.")
    return value


# ------------------------------------------------------------------- auth
class LoginRequest(BaseModel):
    #: A username ("navya") or the full email address. Sent as `identifier`;
    #: `email` is still accepted from older clients and scripts.
    identifier: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("identifier", "email", "username"),
    )
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    """What a successful sign-in returns.

    Deliberately carries NO session secret: that travels only in the
    HttpOnly session cookie. `csrf_token` is not a credential - it is the
    same value as the readable CSRF cookie and is useless without the
    session cookie.
    """

    must_change_password: bool
    user: UserOut
    csrf_token: str


#: The same payload under the name it now actually describes.
LoginResponse = TokenResponse


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=200)
    # Length and content rules live in app.core.passwords.validate_password,
    # which gives a message a person can act on; this is only a sanity cap.
    new_password: str = Field(min_length=1, max_length=200)
    #: Optional for API clients; when sent it must equal new_password.
    confirm_password: str | None = Field(default=None, max_length=200)

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
    username: str | None = None
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
    deactivated_at: datetime | None = None
    created_at: datetime


class UserDetail(UserOut):
    """A user plus the context the Team page needs, resolved server-side.

    Sign-in bookkeeping is included for administrators. Nothing derived from
    a password is - no hash, no plaintext, ever.
    """

    manager_name: str | None = None
    team_name: str | None = None
    heads_department_name: str | None = None
    direct_report_count: int = 0
    # Whether the CALLER may act on this user. The frontend hides buttons
    # from this rather than re-deriving the rule.
    can_act_on: bool = False

    last_login_at: datetime | None = None
    failed_login_count: int = 0
    locked_until: datetime | None = None
    is_locked: bool = False
    password_changed_at: datetime | None = None


def _normalise_email(value: str | None) -> str | None:
    return None if value is None else str(value).strip().lower()


class UserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    #: Optional sign-in name. Blank = derived from the name (core/usernames.py).
    username: str | None = Field(default=None, max_length=60)
    #: Length and content are checked by app.core.passwords in the service,
    #: so the person sees one policy message rather than a schema error.
    password: str = Field(min_length=1, max_length=200)
    confirm_password: str | None = Field(default=None, max_length=200)
    role: Role
    phone: Phone = Field(default=None)
    title: str | None = Field(default=None, max_length=80)
    honorific: Honorific | None = None
    manager_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    #: Force a change at first sign-in. On by default.
    must_change_password: bool = True

    _check = field_validator("password")(_validate_password_bytes)

    @field_validator("email", mode="after")
    @classmethod
    def _email(cls, value: str) -> str:
        return _normalise_email(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _confirmed(self) -> UserCreate:
        if self.confirm_password is not None and self.confirm_password != self.password:
            raise ValueError("The two passwords do not match.")
        return self


class UserUpdate(BaseModel):
    """Every field optional: a PATCH changes only what it names.

    `role` and `manager_id` are here but are gated hard in the router - each
    re-runs the authority checks against the pre-change target.
    """

    name: str | None = Field(default=None, min_length=2, max_length=120)
    email: EmailStr | None = None
    username: str | None = Field(default=None, max_length=60)
    phone: Phone = Field(default=None)
    title: str | None = Field(default=None, max_length=80)
    honorific: Honorific | None = None
    role: Role | None = None
    manager_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    heads_department_id: uuid.UUID | None = None
    is_active: bool | None = None

    @field_validator("email", mode="after")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        return _normalise_email(value)


class SelfUpdate(BaseModel):
    """The strictly smaller set a user may change about themselves.

    Role, manager and active status are absent by design - including for a
    SUPER_ADMIN, so the installation cannot lock itself out.
    """

    name: str | None = Field(default=None, min_length=2, max_length=120)
    phone: Phone = Field(default=None)


class ResetPasswordRequest(BaseModel):
    """Set somebody's password.

    `new_password` is required: the server never invents one, so it never
    holds a plaintext it would have to hand back. Stored passwords are
    one-way hashes and cannot be shown or recovered. The policy itself
    (app.core.passwords) is applied in the service.
    """

    new_password: str = Field(min_length=1, max_length=200)
    #: Optional double-entry check; refused when present and different.
    confirm_password: str | None = Field(default=None, max_length=200)
    #: Whether they must replace it at next sign-in. On by default - a
    #: password two people know is not a password. Turn it off when the point
    #: is to hand somebody a working login they will keep using.
    must_change: bool = True

    _check = field_validator("new_password")(_validate_password_bytes)

    @model_validator(mode="after")
    def _confirmed(self) -> ResetPasswordRequest:
        if self.confirm_password is not None and self.confirm_password != self.new_password:
            raise ValueError("The two passwords do not match.")
        return self


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


class RevealedPassword(BaseModel):
    """Super Admin only. Null when no readable copy of the password exists."""

    password: str | None = None


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


class UserActivityItem(BaseModel):
    """One audit event in a person's activity list. Password material is
    stripped server-side before this is built."""

    id: uuid.UUID
    action: str
    actor_user_id: uuid.UUID | None = None
    actor_name: str | None = None
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    before: dict | None = None
    after: dict | None = None
    ip_address: str | None = None
    created_at: datetime


UserDetail.model_rebuild()
OrgNode.model_rebuild()
TokenResponse.model_rebuild()
