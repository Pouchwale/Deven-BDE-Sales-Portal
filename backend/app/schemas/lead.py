"""Lead and work-queue payloads."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.core.constants import LeadPriority, LeadStatus
from app.schemas.common import ORMModel
from app.schemas.reference import FollowUpDue
from app.core.validators import Phone


class LeadActivityOut(ORMModel):
    id: uuid.UUID
    activity_type: str
    from_status: str | None = None
    to_status: str | None = None
    remark: str | None = None
    created_at: datetime
    actor_user_id: uuid.UUID | None = None
    actor_name: str | None = None
    undone_at: datetime | None = None
    # Whether the CALLER may undo this entry. Derived server-side so the
    # button and the endpoint cannot disagree.
    can_undo: bool = False


class LeadOut(ORMModel):
    id: uuid.UUID
    name: str
    company_name: str | None = None
    mobile: str | None = None
    email: str | None = None
    city: str | None = None
    requirement: str | None = None
    origin: str
    origin_reference_id: uuid.UUID | None = None
    status: str
    priority: str
    assigned_to_user_id: uuid.UUID | None = None
    assigned_by_user_id: uuid.UUID | None = None
    assigned_at: datetime | None = None
    next_follow_up_date: date | None = None
    closed_at: datetime | None = None
    dispatched_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    assigned_to_name: str | None = None
    assigned_by_name: str | None = None
    activity_count: int = 0
    last_activity_at: datetime | None = None
    # How many times ownership has changed hands — a quick red flag without
    # opening the full timeline.
    reassignment_count: int = 0


class LeadDetail(LeadOut):
    activities: list[LeadActivityOut] = []


class LeadCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    company_name: str | None = Field(default=None, max_length=200)
    mobile: Phone = Field(default=None)
    email: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=80)
    requirement: str | None = None
    priority: LeadPriority = LeadPriority.MEDIUM
    next_follow_up_date: date | None = None
    # Who does the work. Must be somebody the caller can act on.
    assigned_to_user_id: uuid.UUID


class LeadUpdate(BaseModel):
    """Every field optional; a PATCH changes only what it names."""

    name: str | None = Field(default=None, min_length=2, max_length=120)
    company_name: str | None = Field(default=None, max_length=200)
    mobile: Phone = Field(default=None)
    email: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=80)
    requirement: str | None = None
    priority: LeadPriority | None = None
    next_follow_up_date: date | None = None
    assigned_to_user_id: uuid.UUID | None = None


class LeadStatusChange(BaseModel):
    status: LeadStatus
    remark: str | None = Field(default=None, max_length=2000)


class LeadActivityCreate(BaseModel):
    activity_type: str
    remark: str | None = Field(default=None, max_length=2000)
    next_follow_up_date: date | None = None


class LeadStats(BaseModel):
    total: int
    open: int
    # One field per pipeline stage, named after the stage. `leads.stats`
    # builds these from the LeadStatus enum, so adding a stage there and
    # forgetting one here fails loudly rather than silently reporting zero.
    new: int
    contacted: int
    not_contacted: int
    nurturing: int
    pre_qualified: int
    qualified: int
    converted: int
    junk: int
    lost: int
    follow_ups_due: int
    # Open leads with no activity logged in the last 7 days.
    no_recent_activity: int


class WorkQueue(BaseModel):
    """The two genuinely different kinds of work, side by side.

    Reference follow-ups are not lead rows — they are surfaced from the
    reference module — so they never inflate the lead counters.
    """

    assigned_leads: list[LeadOut] = []
    reference_follow_ups: list[FollowUpDue] = []
    assigned_total: int = 0
    follow_up_total: int = 0
