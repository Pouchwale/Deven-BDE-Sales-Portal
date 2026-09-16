"""Reference-tracking payloads."""
from __future__ import annotations

import uuid
from datetime import date, datetime  # noqa: F401  (datetime used by AskableAccount)

from pydantic import BaseModel, Field, model_validator

from app.core.constants import ReferenceOutcome
from app.schemas.common import ORMModel
from app.core.validators import Phone


class ReferenceCreate(BaseModel):
    """One recorded ask.

    A `NO` must carry the date to ask again on — "ask me later" without a date
    is not a follow-up, it is a lost thread, and the database CHECK says the
    same thing. Validating it here turns a 500 into a readable 422.

    `NOT_SHARED` is the outcome for a customer who was asked and had nobody to
    refer. It finishes the conversation, so it carries no follow-up date: that
    is exactly what distinguishes it from `NO`.
    """

    # Exactly one: a SAP customer or a converted lead. Both are won deals.
    customer_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    outcome: ReferenceOutcome
    asked_on: date | None = None
    next_reference_date: date | None = None

    referred_name: str | None = Field(default=None, max_length=120)
    referred_company: str | None = Field(default=None, max_length=200)
    referred_mobile: Phone = Field(default=None)
    referred_email: str | None = Field(default=None, max_length=255)
    notes: str | None = None

    # Attribution. Defaults to the caller; a manager may credit somebody they
    # can act on, and nobody else.
    requested_by_user_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _check_outcome(self) -> ReferenceCreate:
        if (self.customer_id is None) == (self.lead_id is None):
            raise ValueError(
                "An ask belongs to exactly one account — a customer or a converted lead."
            )
        if self.outcome == ReferenceOutcome.NO and self.next_reference_date is None:
            raise ValueError(
                "Choose when to ask again, or record it as not shared."
            )
        if self.outcome == ReferenceOutcome.YES and not (
            self.referred_name or self.referred_company or self.referred_mobile
        ):
            raise ValueError(
                "A reference needs at least a name, company or mobile number."
            )
        return self


class ReferenceOut(ORMModel):
    id: uuid.UUID
    customer_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    requested_by_user_id: uuid.UUID | None = None
    outcome: str
    asked_on: date
    next_reference_date: date | None = None
    referred_name: str | None = None
    referred_company: str | None = None
    referred_mobile: str | None = None
    referred_email: str | None = None
    notes: str | None = None
    converted_lead_id: uuid.UUID | None = None
    created_at: datetime

    # Resolved server-side so a list does not need a second round trip.
    # `source_name` is whichever account gave the reference, so the UI does not
    # have to branch on which of the two ids is set.
    customer_name: str | None = None
    customer_sap_code: str | None = None
    source_name: str | None = None
    source_type: str = "CUSTOMER"
    requested_by_name: str | None = None


class FollowUpDue(BaseModel):
    """A won account whose "ask me later" date has arrived.

    The subject is always a converted LEAD. `subject_type` is kept so the
    field does not have to be re-added the day a second kind of subject
    exists, but the SAP customer book is no longer one of them.

    Still not a lead *pipeline* row: a follow-up is surfaced as a work item
    and never inflates the lead counts. (plan v3 s7.2)
    """

    subject_type: str = "LEAD"
    subject_id: uuid.UUID
    subject_name: str
    company_name: str | None = None
    mobile: str | None = None
    email: str | None = None
    owner_user_id: uuid.UUID | None = None
    owner_name: str | None = None
    next_reference_date: date
    last_reference_asked_at: date | None = None
    days_overdue: int
    decline_count: int = 0


class AskableAccount(BaseModel):
    """A won account that can be asked to refer somebody.

    ONE kind of subject: a lead this portal saw converted, whose post-sale
    record says it was invoiced at least ten days ago. The SAP customer book
    used to be the other kind, which is how this module could report 17
    accounts while Assigned Leads reported 22 conversions - two universes with
    no row in common, both honestly reporting their own.
    """

    subject_type: str = "LEAD"
    subject_id: uuid.UUID
    subject_name: str
    company_name: str | None = None
    mobile: str | None = None
    email: str | None = None
    owner_user_id: uuid.UUID | None = None
    owner_name: str | None = None
    reference_status: str
    last_reference_asked_at: date | None = None
    next_reference_date: date | None = None
    decline_count: int = 0
    #: The sheet's Reference Date - the day this account became askable. Shown
    #: so a row says WHY it is in the queue, in the business's own terms.
    reference_date: date | None = None
    converted_at: datetime | None = None


class ReferenceStats(BaseModel):
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
    #: 10 eligible with 7 references reads -30.0; all asked reads 0.0.
    reference_score: float = 0.0


