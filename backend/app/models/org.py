"""Teams, departments and users - the reporting spine."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import ROLE_RANK, Role
from app.db.base import GUID, Base, TimestampType, new_uuid, utcnow


class Team(Base):
    """A grouping label. Grants nothing - authority flows through manager_id."""

    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )

    members: Mapped[list[User]] = relationship(
        "User", back_populates="team", foreign_keys="User.team_id"
    )


class Department(Base):
    """The dimension feedback is rated against."""

    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )

    head: Mapped[User | None] = relationship(
        "User", back_populates="heads_department", uselist=False
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(30))
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=Role.BDE)
    title: Mapped[str | None] = mapped_column(String(80))

    # The whole visibility model hangs off this one column.
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("teams.id", ondelete="SET NULL")
    )
    # An attribute, not a role: grants a feedback read scope and nothing else.
    heads_department_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("departments.id", ondelete="SET NULL")
    )

    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    plain_password: Mapped[str | None] = mapped_column(String(255), nullable=True, default="ChangeMe@123")
    #: Optional and admin-set. Null for most people, who are addressed by
    #: name. Never derived from the name or the role - see constants.Honorific.
    honorific: Mapped[str | None] = mapped_column(String(10))

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    password_changed_at: Mapped[datetime | None] = mapped_column(TimestampType)
    deactivated_at: Mapped[datetime | None] = mapped_column(TimestampType)

    created_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        TimestampType, nullable=False, default=utcnow, onupdate=utcnow
    )

    manager: Mapped[User | None] = relationship(
        "User", remote_side="User.id", back_populates="reports", foreign_keys=[manager_id]
    )
    reports: Mapped[list[User]] = relationship(
        "User", back_populates="manager", foreign_keys=[manager_id]
    )
    team: Mapped[Team | None] = relationship(
        "Team", back_populates="members", foreign_keys=[team_id]
    )
    heads_department: Mapped[Department | None] = relationship(
        "Department", back_populates="head", foreign_keys=[heads_department_id]
    )

    # ------------------------------------------------------------ helpers
    # Read-only conveniences. Every authority DECISION lives in
    # app/core/authority.py, never here.
    @property
    def rank(self) -> int:
        return ROLE_RANK[self.role]

    def __repr__(self) -> str:
        return f"<User {self.name} ({self.role})>"
