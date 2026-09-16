"""The contractual vocabularies.

Every enumerated value in the system is defined exactly once, here. The
database CHECK constraints in app/db/sql/0001_initial.sql list the same
strings; tests/test_schema_parity.py asserts the two agree, so adding a
status in one place and forgetting the other fails the build rather than
failing at 2am against real data.

No literal status string appears anywhere else in the codebase.
"""
from __future__ import annotations

from enum import StrEnum


# ------------------------------------------------------------------ roles
class Honorific(StrEnum):
    """How a person is addressed, when they are addressed that way.

    Set by an administrator, never derived. Guessing it from a first name is
    guessing somebody's gender from a string; deriving it from rank would
    address every manager identically regardless of what they go by.
    """

    SIR = "SIR"
    MAAM = "MAAM"


class Role(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    BDE = "BDE"
    SALES = "SALES"


ALL_ROLES: tuple[str, ...] = tuple(r.value for r in Role)

# Rank is derived, never stored. LOWER NUMBER = HIGHER AUTHORITY.
# BDE and SALES share rank 3 deliberately: neither may act on the other.
ROLE_RANK: dict[str, int] = {
    Role.SUPER_ADMIN: 0,
    Role.ADMIN: 1,
    Role.MANAGER: 2,
    Role.BDE: 3,
    Role.SALES: 3,
}

ROLE_LABELS: dict[str, str] = {
    Role.SUPER_ADMIN: "Super Admin",
    Role.ADMIN: "Admin",
    Role.MANAGER: "Manager",
    Role.BDE: "BDE",
    Role.SALES: "Sales",
}

# Sees every user and every row, regardless of the reporting chain.
ADMIN_ROLES: tuple[str, ...] = (Role.SUPER_ADMIN, Role.ADMIN)
# May open a team view at all.
LEADERSHIP_ROLES: tuple[str, ...] = (Role.SUPER_ADMIN, Role.ADMIN, Role.MANAGER)


# ------------------------------------------------------------------ teams
class TeamCode(StrEnum):
    MANAGEMENT = "MGMT"
    BDE = "BDE"
    SALES = "SALES"


# -------------------------------------------------------------- customers
class ReferenceStatus(StrEnum):
    """Per-account roll-up of "where has this account got to?".

    NOT_ASKED  nobody has asked yet                        -> actionable
    PENDING    asked, "not right now", follow-up date set  -> actionable later
    TAKEN      gave a reference                            -> done
    DECLINED   asked, said no reference to give            -> done
    """

    NOT_ASKED = "NOT_ASKED"
    TAKEN = "TAKEN"
    PENDING = "PENDING"
    DECLINED = "DECLINED"


class ReferenceOutcome(StrEnum):
    """What the customer actually answered when asked to refer somebody.

    Three answers, two of which finish the conversation:

      YES         gave a reference        -> completed, and a reference received
      NO          not right now           -> pending, owes a follow-up date
      NOT_SHARED  asked, none given       -> completed, nothing to chase

    NO is the only one that comes back round. Keeping NOT_SHARED separate from
    NO is the whole point: before it existed, a customer who had genuinely
    declined was indistinguishable from one who had asked to be called later,
    so they stayed in the follow-up queue forever.
    """

    YES = "YES"
    NO = "NO"
    NOT_SHARED = "NOT_SHARED"


#: Outcomes that END the reference conversation. Both count towards "requests
#: completed"; only YES counts towards "references received" - see
#: `references.stats`, which deliberately reports the two separately.
COMPLETED_REFERENCE_OUTCOMES: tuple[str, ...] = (
    ReferenceOutcome.YES,
    ReferenceOutcome.NOT_SHARED,
)

#: The per-account roll-up states that mean "do not ask this account again".
COMPLETED_REFERENCE_STATUSES: tuple[str, ...] = (
    ReferenceStatus.TAKEN,
    ReferenceStatus.DECLINED,
)


# ------------------------------------------------------------------ leads
class LeadOrigin(StrEnum):
    """The two genuinely different kinds of work (plan v3 s7)."""

    ASSIGNED_BY_HEAD = "ASSIGNED_BY_HEAD"
    REFERENCE_FOLLOWUP = "REFERENCE_FOLLOWUP"


class LeadStatus(StrEnum):
    """Where a lead has got to.

    NEW is where every assigned lead starts. From there the pipeline has two
    entrances - somebody picked up, or they did not - and one exit that counts
    as a win.

    RETIRED_LEAD_STATUSES below holds the names this enum used to use. They are
    gone from the pipeline but still appear in `lead_activities.from_status` /
    `to_status`, because that history records what really happened and is not
    rewritten.
    """

    NEW = "NEW"
    CONTACTED = "CONTACTED"
    NOT_CONTACTED = "NOT_CONTACTED"
    NURTURING = "NURTURING"
    PRE_QUALIFIED = "PRE_QUALIFIED"
    QUALIFIED = "QUALIFIED"
    CONVERTED = "CONVERTED"
    JUNK = "JUNK"
    LOST = "LOST"


#: Names this enum used before September 2026, kept so old timeline entries
#: still render a label instead of falling through as raw text. Nothing can be
#: SET to one of these; they exist only to be read back.
RETIRED_LEAD_STATUSES: dict[str, str] = {
    "PENDING": "New",          # became NEW
    "FOLLOW_UP": "Nurturing",  # became NURTURING
}


class LeadPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


OPEN_LEAD_STATUSES: tuple[str, ...] = (
    LeadStatus.NEW,
    LeadStatus.CONTACTED,
    LeadStatus.NOT_CONTACTED,
    LeadStatus.NURTURING,
    LeadStatus.PRE_QUALIFIED,
    LeadStatus.QUALIFIED,
)

#: Nothing follows these. CONVERTED is the win; LOST and JUNK are the two ways
#: a lead stops being work.
CLOSED_LEAD_STATUSES: tuple[str, ...] = (
    LeadStatus.CONVERTED,
    LeadStatus.LOST,
    LeadStatus.JUNK,
)

# ---------------------------------------------------------------------------
# The pipeline, as data.
#
#   NEW ─┬─► CONTACTED ──► NURTURING ──► PRE_QUALIFIED ──► QUALIFIED ──► CONVERTED
#        │       │              │               │              │
#        ├─► NOT_CONTACTED ─────┘               │              │
#        │       │                              │              │
#        ├─► JUNK                               ▼              ▼
#        └─► LOST ◄────────────────────────── LOST ◄────────── LOST
#
# Two rules worth saying out loud, because they are what the old table got
# wrong:
#
#   * A stage cannot be skipped. CONTACTED → QUALIFIED is refused; a lead has
#     to be nurtured and pre-qualified on the way. That is the point of having
#     the stages at all - a pipeline you can jump to the end of measures
#     nothing.
#   * CONVERTED, LOST and JUNK are FINAL. There is no path back. A mis-click
#     is corrected by an administrator reopening it (see `leads.reopen`), which
#     is recorded like any other change rather than erasing the mistake.
# ---------------------------------------------------------------------------
ALLOWED_LEAD_TRANSITIONS: dict[str, frozenset[str]] = {
    # Four ways out of a brand new lead: reached them, could not reach them,
    # it was never a real lead, or it died immediately.
    LeadStatus.NEW: frozenset(
        {
            LeadStatus.CONTACTED,
            LeadStatus.NOT_CONTACTED,
            LeadStatus.JUNK,
            LeadStatus.LOST,
        }
    ),
    LeadStatus.CONTACTED: frozenset(
        {LeadStatus.NURTURING, LeadStatus.JUNK, LeadStatus.LOST}
    ),
    # Could not reach them yet. Reaching them later is the normal path; it can
    # also turn out to be junk, or simply die.
    LeadStatus.NOT_CONTACTED: frozenset(
        {LeadStatus.CONTACTED, LeadStatus.JUNK, LeadStatus.LOST}
    ),
    LeadStatus.NURTURING: frozenset({LeadStatus.PRE_QUALIFIED, LeadStatus.LOST}),
    LeadStatus.PRE_QUALIFIED: frozenset({LeadStatus.QUALIFIED, LeadStatus.LOST}),
    LeadStatus.QUALIFIED: frozenset({LeadStatus.CONVERTED, LeadStatus.LOST}),
    LeadStatus.CONVERTED: frozenset(),
    LeadStatus.LOST: frozenset(),
    LeadStatus.JUNK: frozenset(),
}

#: Where an administrator may put a lead that was closed by mistake. Not a
#: transition anyone else can make, and not a way back into the middle of the
#: pipeline - it returns to the start so the stages are walked again honestly.
REOPEN_LEAD_STATUS: str = LeadStatus.NEW


class LeadActivityType(StrEnum):
    ASSIGNED = "ASSIGNED"
    REASSIGNED = "REASSIGNED"
    STATUS_CHANGED = "STATUS_CHANGED"
    NOTE = "NOTE"
    CALL = "CALL"
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"
    MEETING = "MEETING"
    FOLLOW_UP_SCHEDULED = "FOLLOW_UP_SCHEDULED"
    FEEDBACK_REQUESTED = "FEEDBACK_REQUESTED"


class CustomerActivityType(StrEnum):
    """The timeline of a COMPLETED customer.

    A customer from SAP has already finished the lead pipeline, so what is
    still open about it is the feedback and the reference - not a stage.
    """

    NOTE = "NOTE"
    CALL = "CALL"
    WHATSAPP = "WHATSAPP"
    EMAIL = "EMAIL"
    MEETING = "MEETING"
    FEEDBACK_REQUESTED = "FEEDBACK_REQUESTED"
    FEEDBACK_RECEIVED = "FEEDBACK_RECEIVED"
    REVIEW_REQUESTED = "REVIEW_REQUESTED"
    REFERENCE_ASKED = "REFERENCE_ASKED"


# Customer timeline entries a user may add by hand. FEEDBACK_RECEIVED and
# REFERENCE_ASKED are written by the system when those things actually happen.
#
# REVIEW_REQUESTED is deliberately absent: the Google review feature was
# removed. The enum member above stays so the timeline entries already written
# still render their label instead of falling through as an unknown type.
MANUAL_CUSTOMER_ACTIVITY_TYPES: tuple[str, ...] = (
    CustomerActivityType.NOTE,
    CustomerActivityType.CALL,
    CustomerActivityType.WHATSAPP,
    CustomerActivityType.EMAIL,
    CustomerActivityType.MEETING,
    CustomerActivityType.FEEDBACK_REQUESTED,
)


# Activity types a user may add by hand. The rest are system generated.
MANUAL_ACTIVITY_TYPES: tuple[str, ...] = (
    LeadActivityType.NOTE,
    LeadActivityType.CALL,
    LeadActivityType.WHATSAPP,
    LeadActivityType.EMAIL,
    LeadActivityType.MEETING,
    LeadActivityType.FOLLOW_UP_SCHEDULED,
    LeadActivityType.FEEDBACK_REQUESTED,
)


# --------------------------------------------------------------- feedback
class FeedbackSource(StrEnum):
    INTERNAL = "INTERNAL"
    PUBLIC_LINK = "PUBLIC_LINK"
    GOOGLE_FORMS_IMPORT = "GOOGLE_FORMS_IMPORT"
    GOOGLE_FORMS_WEBHOOK = "GOOGLE_FORMS_WEBHOOK"


class FeedbackRequestStatus(StrEnum):
    """The lifecycle of an ask.

    There is no PENDING: "not yet asked" is the ABSENCE of a request, and a
    row per askable record would duplicate the derived queue in
    feedback_analysis.pending_requests and then drift from it.
    """

    #: The code exists and a message was drafted. Nothing is proven sent -
    #: composing has to issue the code, because the code is part of the link.
    ISSUED = "ISSUED"
    #: The assignee confirmed it went out. Only this reads as "awaiting".
    SENT = "SENT"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


#: A request that can still be answered or sent - reused, never duplicated.
OPEN_FEEDBACK_REQUEST_STATUSES: frozenset[str] = frozenset(
    {FeedbackRequestStatus.ISSUED, FeedbackRequestStatus.SENT}
)


class FeedbackMatchStatus(StrEnum):
    """How a response found the record it is about - a property of the
    RESPONSE, not of the request: an unmatched one has no request to sit on."""

    MATCHED_TOKEN = "MATCHED_TOKEN"
    MATCHED_CONTACT = "MATCHED_CONTACT"
    UNMATCHED = "UNMATCHED"
    DUPLICATE = "DUPLICATE"


class SyncEventStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    DUPLICATE = "DUPLICATE"


class SyncSource(StrEnum):
    WEBHOOK = "WEBHOOK"
    PULL = "PULL"
    FILE = "FILE"


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ImportStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class FeedbackImportSource(StrEnum):
    GOOGLE_FORMS_XLSX = "GOOGLE_FORMS_XLSX"
    # Not a real import. Marks the demonstration batch created by
    # app/seeds/sample_feedback.py, which the API reports as `is_sample` so
    # the UI can badge it and nobody mistakes it for a customer's opinion.
    SAMPLE = "SAMPLE"


class SapImportSource(StrEnum):
    SAP_FILE = "SAP_FILE"
    SAP_B1_DB = "SAP_B1_DB"


# ---------------------------------------------------------- notifications
class NotificationType(StrEnum):
    LEAD_ASSIGNED = "LEAD_ASSIGNED"
    LEAD_REASSIGNED = "LEAD_REASSIGNED"
    REFERENCE_FOLLOWUP_DUE = "REFERENCE_FOLLOWUP_DUE"
    DEPARTMENT_ALERT = "DEPARTMENT_ALERT"
    FEEDBACK_RECEIVED = "FEEDBACK_RECEIVED"
    FEEDBACK_UNMATCHED = "FEEDBACK_UNMATCHED"


# ----------------------------------------------------------------- audit
class AuditAction(StrEnum):
    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    ROLE_CHANGED = "ROLE_CHANGED"
    REPORTING_LINE_CHANGED = "REPORTING_LINE_CHANGED"
    PASSWORD_RESET = "PASSWORD_RESET"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    USER_DEACTIVATED = "USER_DEACTIVATED"
    USER_REACTIVATED = "USER_REACTIVATED"
    USER_DELETED = "USER_DELETED"
    DEPARTMENT_HEAD_SET = "DEPARTMENT_HEAD_SET"
    LEAD_CREATED = "LEAD_CREATED"
    LEAD_UPDATED = "LEAD_UPDATED"
    LEAD_ASSIGNED = "LEAD_ASSIGNED"
    LEAD_REASSIGNED = "LEAD_REASSIGNED"
    LEAD_STATUS_CHANGED = "LEAD_STATUS_CHANGED"
    REFERENCE_RECORDED = "REFERENCE_RECORDED"
    CUSTOMER_OWNER_CHANGED = "CUSTOMER_OWNER_CHANGED"
    ACTIVITY_UNDONE = "ACTIVITY_UNDONE"
    REVIEW_REQUESTED = "REVIEW_REQUESTED"
    SAP_IMPORTED = "SAP_IMPORTED"
    FEEDBACK_IMPORTED = "FEEDBACK_IMPORTED"
    FEEDBACK_REQUEST_ISSUED = "FEEDBACK_REQUEST_ISSUED"
    FEEDBACK_REQUEST_SENT = "FEEDBACK_REQUEST_SENT"
    FEEDBACK_REQUEST_CANCELLED = "FEEDBACK_REQUEST_CANCELLED"
    FEEDBACK_RECEIVED = "FEEDBACK_RECEIVED"
    FEEDBACK_RESOLVED = "FEEDBACK_RESOLVED"
    FEEDBACK_SYNCED = "FEEDBACK_SYNCED"
    FEEDBACK_ALERT_ASSIGNED = "FEEDBACK_ALERT_ASSIGNED"
    SETTINGS_UPDATED = "SETTINGS_UPDATED"
    # One row per tool the assistant ran, so an answer can be traced back to
    # the reads that produced it.
    CHAT_TOOL_INVOKED = "CHAT_TOOL_INVOKED"


class EntityType(StrEnum):
    USER = "USER"
    LEAD = "LEAD"
    CUSTOMER = "CUSTOMER"
    REFERENCE = "REFERENCE"
    FEEDBACK = "FEEDBACK"
    FEEDBACK_REQUEST = "FEEDBACK_REQUEST"
    SETTING = "SETTING"
    IMPORT = "IMPORT"
    CHAT = "CHAT"


# ----------------------------------------------------------- error codes
class ErrorCode(StrEnum):
    """Machine-readable codes returned in the error envelope."""

    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CONFLICT = "CONFLICT"

    ROLE_ABOVE_ACTOR = "ROLE_ABOVE_ACTOR"
    CYCLIC_REPORTING_LINE = "CYCLIC_REPORTING_LINE"
    HAS_DIRECT_REPORTS = "HAS_DIRECT_REPORTS"
    UNMAPPED_COLUMNS = "UNMAPPED_COLUMNS"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    MANAGER_RANK_INVALID = "MANAGER_RANK_INVALID"
    DEPARTMENT_ALREADY_HEADED = "DEPARTMENT_ALREADY_HEADED"
