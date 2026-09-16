"""Reading a Google Forms export whose column names nobody controls.

Google Forms headers are whole question sentences, they change the moment
somebody edits the form, and a silent mismapping corrupts the analysis
invisibly. So nothing here guesses: a header either matches, or it is reported
to the admin as unmapped and the dry run shows exactly what was resolved.

Resolution is three passes (plan v3 s8.2):
  1. Exact match on the normalised header.
  2. Contains match against configured key phrases.
  3. Unmapped — reported, never guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.core import text

_WHITESPACE = re.compile(r"\s+")

#: `  How would you RATE  us? ` -> `how would you rate us?`. Re-exported from
#: app.core.text so the mapper, the SAP importer and the ingest all key on the
#: same normalisation.
normalise_header = text.normalise_header


# Our field -> phrases that identify it. Order matters: the first field whose
# phrase is found wins, so put the specific ones first.
FEEDBACK_COLUMN_MAP: dict[str, tuple[str, ...]] = {
    "submitted_at_source": ("timestamp", "submission time", "date submitted"),
    "customer_name": ("customer name", "your name", "full name", "name"),
    "company_name": ("company name", "firm name", "organisation", "organization", "company"),
    "mobile": ("mobile number", "phone number", "contact number", "mobile", "phone", "contact"),
    "email": ("email address", "e-mail", "email"),
    "handled_by_name": (
        "bde / salesperson who handled you",
        "salesperson",
        "sales person",
        "who handled you",
        "handled by",
        "bde",
    ),
    "overall_rating": (
        "overall, how satisfied are you",
        "overall satisfaction",
        "overall rating",
        "rate your overall experience",
        "overall experience",
        "overall",
    ),
    "would_recommend": ("would you recommend", "recommend us", "recommend"),
    "overall_comments": (
        "any other comments or suggestions",
        "additional comments",
        "suggestions",
        "other comments",
    ),
}

# A department rating column, e.g. "How would you rate our Production team?" or
# "Rate our Dispatch department". The captured group is the department name.
DEPARTMENT_RATING_PATTERNS: tuple[str, ...] = (
    r"^(?:how would you )?rate (?:our |the )?(?P<dept>.+?)\s*(?:team|department|dept)\b",
    r"^(?P<dept>.+?)\s*(?:team|department)\s*rating",
)

# The free-text field that accompanies a department rating.
DEPARTMENT_COMMENT_PATTERNS: tuple[str, ...] = (
    r"^any comments? (?:about|on|for)\s+(?P<dept>.+?)\??$",
    r"^comments? (?:about|on|for)\s+(?P<dept>.+?)\??$",
    r"^(?P<dept>.+?)\s*(?:team|department)?\s*comments?\??$",
)


@dataclass(frozen=True, slots=True)
class ColumnResolution:
    """What the dry run shows the admin before anything is written."""

    fields: dict[str, str]                      # our field -> original header
    department_ratings: dict[str, str]          # department name -> header
    department_comments: dict[str, str]         # department name -> header
    unmapped: list[str]                         # headers we could not place

    def as_dict(self) -> dict:
        return {
            "fields": self.fields,
            "department_ratings": self.department_ratings,
            "department_comments": self.department_comments,
            "unmapped": self.unmapped,
        }


def _match_department(header: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.match(pattern, header, re.IGNORECASE)
        if match:
            name = _WHITESPACE.sub(" ", match.group("dept")).strip(" ?:-")
            if name:
                return name.title()
    return None


def resolve_columns(headers: list[str]) -> ColumnResolution:
    """Map a form's headers onto our fields and its departments."""
    fields: dict[str, str] = {}
    department_ratings: dict[str, str] = {}
    department_comments: dict[str, str] = {}
    unmapped: list[str] = []

    for header in headers:
        normalised = normalise_header(header)
        if not normalised:
            continue

        # 1. Department columns first — "How would you rate our Sales team?"
        #    also contains "rate", which the overall-rating phrases would
        #    otherwise claim.
        comment_dept = _match_department(normalised, DEPARTMENT_COMMENT_PATTERNS)
        rating_dept = _match_department(normalised, DEPARTMENT_RATING_PATTERNS)

        if rating_dept and rating_dept not in department_ratings:
            department_ratings[rating_dept] = header
            continue
        if comment_dept and comment_dept not in department_comments:
            department_comments[comment_dept] = header
            continue

        # 2. Exact match on a configured phrase.
        placed = False
        for field, phrases in FEEDBACK_COLUMN_MAP.items():
            if field in fields:
                continue
            if normalised in phrases:
                fields[field] = header
                placed = True
                break
        if placed:
            continue

        # 3. Contains match.
        for field, phrases in FEEDBACK_COLUMN_MAP.items():
            if field in fields:
                continue
            if any(phrase in normalised for phrase in phrases):
                fields[field] = header
                placed = True
                break

        if not placed:
            unmapped.append(header)

    return ColumnResolution(fields, department_ratings, department_comments, unmapped)


# ------------------------------------------------------------- values
# Worded scales seen on Indian customer-feedback forms, lowest to highest.
WORDED_SCALES: dict[str, int] = {
    "very dissatisfied": 1,
    "very poor": 1,
    "terrible": 1,
    "poor": 2,
    "dissatisfied": 2,
    "bad": 2,
    "average": 3,
    "neutral": 3,
    "okay": 3,
    "ok": 3,
    "satisfied": 4,
    "good": 4,
    "very satisfied": 5,
    "excellent": 5,
    "very good": 5,
    "outstanding": 5,
}

_LEADING_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:/\s*(\d+))?")


def parse_rating(raw: str | None, *, scale_max: int) -> Decimal | None:
    """Turn a form answer into a number on our scale, or None.

    Handles `4`, `4 - Satisfied`, `8/10`, `Excellent`. Anything else returns
    None rather than a guess — a wrong rating is worse than a missing one.
    """
    text = (raw or "").strip()
    if not text:
        return None

    match = _LEADING_NUMBER.match(text)
    if match:
        try:
            value = Decimal(match.group(1))
        except InvalidOperation:  # pragma: no cover - regex guarantees a number
            return None
        source_max = Decimal(match.group(2)) if match.group(2) else None
        if source_max is None:
            # "8" on a form whose top is 10 still has to land on our scale, but
            # the row alone cannot say what the form's top was. Values above
            # our max are rescaled from the nearest common scale.
            source_max = Decimal(10) if value > scale_max else Decimal(scale_max)
        if source_max <= 0:
            return None
        return _rescale(value, source_max, scale_max)

    lowered = normalise_header(text)
    # Longest phrase first, so "very satisfied" beats "satisfied".
    for phrase in sorted(WORDED_SCALES, key=len, reverse=True):
        if phrase in lowered:
            return _rescale(Decimal(WORDED_SCALES[phrase]), Decimal(5), scale_max)
    return None


def _rescale(value: Decimal, source_max: Decimal, scale_max: int) -> Decimal:
    if source_max == scale_max:
        result = value
    else:
        result = value / source_max * Decimal(scale_max)
    clamped = max(Decimal(0), min(result, Decimal(scale_max)))
    return clamped.quantize(Decimal("0.01"))


_TIMESTAMP_FORMATS = (
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%d-%m-%Y %H:%M:%S",
    "%d/%m/%Y",
    "%Y-%m-%d",
)


def parse_timestamp(raw: str | None) -> datetime | None:
    """Parse the Google Forms Timestamp column, day-first."""
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
