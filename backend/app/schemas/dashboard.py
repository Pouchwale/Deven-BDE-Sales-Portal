"""Dashboard payloads.

Three shapes from one endpoint, so the frontend switches on `shape` rather
than guessing from the caller's role. Everything is grouped by module, which
is how the page is laid out and how a head reads it.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ReferenceKpis(BaseModel):
    # The won book, and how much of it is actionable yet. These four always
    # add up: eligible_accounts + waiting_period + awaiting_sync == converted_leads.
    converted_leads: int = 0
    eligible_accounts: int = 0
    awaiting_sync: int = 0
    waiting_period: int = 0
    # Two different numbers on purpose:
    #   references_taken    a reference was actually given
    #   requests_completed  the ask is finished (given OR none to give)
    references_taken: int = 0
    requests_completed: int = 0
    references_pending: int = 0
    references_declined: int = 0
    not_asked: int = 0
    follow_ups_due: int = 0
    completion_rate: float = 0.0
    reference_rate: float = 0.0
    #: The agreed score: -(eligible accounts still owing a reference) %.
    #: 10 accounts with 7 references reads -30.0; all asked reads 0.0.
    reference_score: float = 0.0


class PersonalReferenceScore(BaseModel):
    """A manager's score on the accounts assigned to them personally, beside
    the team score in `references`."""

    eligible_accounts: int = 0
    references_taken: int = 0
    reference_score: float = 0.0


class LeadKpis(BaseModel):
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
    no_recent_activity: int


class FeedbackKpis(BaseModel):
    total_responses: int
    imported_responses: int
    average_rating: float | None = None
    rating_scale_max: int
    departments_below_threshold: int
    open_alerts: int


class OrgKpis(BaseModel):
    """The people around the caller, and what is waiting for them.

    The invoice counters that used to live here came from the SAP book and
    described a universe no lead belonged to, so they moved nothing and
    explained nothing. Headcount and unread notifications are what is left,
    and both are about the caller's actual scope.
    """

    users_in_scope: int
    active_users: int
    unread_notifications: int


class DepartmentRating(BaseModel):
    department_id: uuid.UUID
    department_name: str
    average_rating: float | None = None
    response_count: int
    below_threshold: bool


class MonthlyVolume(BaseModel):
    month: str
    responses: int


class TeamRollup(BaseModel):
    """Headcount and pipeline per team - leads, not the SAP customer book.

    A team's "customers" used to be counted from SAP ownership, which had no
    relationship to anything those people worked on.
    """

    team_name: str
    members: int
    open_leads: int
    converted: int


class ReportRow(BaseModel):
    """One row per person in the caller's subtree.

    "How is each of my people doing" is the question the reporting chain
    exists to answer, so this is the manager dashboard's centrepiece.

    Every field is about LEADS - the work these people actually do. The
    columns this replaced (customers, invoices, last invoice, and a per-person
    feedback rating) all came from the SAP book, which had no link to any
    lead: somebody carrying forty open leads showed "0 customers" and read as
    idle.
    """

    user_id: uuid.UUID
    name: str
    role: str
    title: str | None = None
    team_name: str | None = None
    is_active: bool
    open_leads: int
    converted: int
    references_taken: int
    followups_due: int
    #: The reference score and the two numbers behind it, over this person's
    #: own eligible accounts. Same formula as the module KPI.
    eligible_accounts: int = 0
    references_on_eligible: int = 0
    reference_score: float = 0.0
    #: Last time this person did anything on a lead of theirs.
    last_activity_at: datetime | None = None


class AlertBrief(BaseModel):
    department_id: uuid.UUID
    department_name: str
    average_rating: float
    response_count: int
    threshold: float


class ImportHealth(BaseModel):
    filename: str
    status: str
    total_rows: int
    created_count: int
    skipped_count: int
    error_count: int
    created_at: datetime


class DashboardOut(BaseModel):
    shape: Literal["ADMIN", "MANAGER", "PERSONAL"]
    generated_at: datetime
    user_name: str
    role: str

    references: ReferenceKpis
    #: Managers only: their own accounts, apart from the team's.
    my_reference: PersonalReferenceScore | None = None
    leads: LeadKpis
    org: OrgKpis
    # Present only when the caller may read feedback at all — an admin or a
    # flagged department head. A plain manager gets null, and the UI hides the
    # whole section rather than showing zeroes it is not entitled to.
    feedback: FeedbackKpis | None = None
    # Converted leads and customers still owed a feedback ask. Scoped by the
    # reporting chain rather than by department, so everyone sees their own
    # outstanding asks even without rights to the analysis above.
    feedback_pending: int = 0

    department_ratings: list[DepartmentRating] = []
    monthly_feedback: list[MonthlyVolume] = []
    alerts: list[AlertBrief] = []

    teams: list[TeamRollup] = []          # ADMIN
    reports: list[ReportRow] = []         # ADMIN + MANAGER
    imports: list[ImportHealth] = []      # ADMIN
