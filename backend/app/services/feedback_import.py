"""Importing the Google Forms export.

Two phases, and the first is not optional (plan v3 s8.1):

  DRY RUN  resolve the columns, parse five sample rows, commit nothing, and
           show the admin exactly what mapped to what.
  COMMIT   write the batch under the mapping the admin confirmed, deduped on a
           hash of the normalised source row so a re-import creates nothing.

Departments are discovered from the form, not invented. An unmatched name is
reported so the admin can map it or create it — the alternative is a silent
new department nobody meant to have.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.constants import (
    AuditAction,
    EntityType,
    FeedbackImportSource,
    FeedbackMatchStatus,
    FeedbackRequestStatus,
    FeedbackSource,
    ImportStatus,
)
from app.models.feedback import Feedback, FeedbackImport
from app.models.org import Department, User
from app.services import audit, feedback_ingest, feedback_requests
from app.services.feedback_mapping import ColumnResolution, resolve_columns

_WHITESPACE = re.compile(r"\s+")

SAMPLE_ROWS = 5


# --------------------------------------------------------------- reading
def read_rows(content: bytes, filename: str) -> list[dict[str, str]]:
    """Read an .xlsx or .csv export into plain string rows."""
    if filename.lower().endswith((".xlsx", ".xls")):
        import pandas as pd

        frame = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
        return [
            {str(k): ("" if v is None else str(v)) for k, v in row.items()}
            for row in frame.to_dict(orient="records")
        ]

    text = content.decode("utf-8-sig", errors="replace")
    return [dict(row) for row in csv.DictReader(io.StringIO(text))]


def row_hash(row: dict[str, str], resolution: ColumnResolution) -> str:
    """Identity of one response.

    Built from the mapped columns only, so an extra column appearing in a
    later export does not make every existing response look new.
    """
    headers = (
        list(resolution.fields.values())
        + list(resolution.department_ratings.values())
        + list(resolution.department_comments.values())
    )
    parts = [
        _WHITESPACE.sub(" ", str(row.get(header, "")).strip()).lower()
        for header in sorted(headers)
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# --------------------------------------------------------------- results
@dataclass
class DryRunResult:
    filename: str
    total_rows: int
    resolution: ColumnResolution
    samples: list[dict]
    known_departments: list[str]
    unknown_departments: list[str]
    missing_fields: list[str]
    duplicate_rows: int
    warnings: list[str] = field(default_factory=list)

    @property
    def can_commit(self) -> bool:
        # An overall rating or at least one department rating is the minimum
        # that makes a response worth storing.
        return self.total_rows > 0 and (
            "overall_rating" in self.resolution.fields
            or bool(self.resolution.department_ratings)
        )

    def as_dict(self) -> dict:
        return {
            "filename": self.filename,
            "total_rows": self.total_rows,
            "column_map": self.resolution.as_dict(),
            "samples": self.samples,
            "known_departments": self.known_departments,
            "unknown_departments": self.unknown_departments,
            "missing_fields": self.missing_fields,
            "duplicate_rows": self.duplicate_rows,
            "warnings": self.warnings,
            "can_commit": self.can_commit,
        }


@dataclass
class CommitResult:
    import_id: uuid.UUID | None
    filename: str
    total_rows: int
    created_count: int
    skipped_count: int
    error_count: int
    errors: list[dict]
    ratings_created: int
    departments_created: list[str]
    status: str

    def as_dict(self) -> dict:
        return {
            "import_id": str(self.import_id) if self.import_id else None,
            "filename": self.filename,
            "total_rows": self.total_rows,
            "created_count": self.created_count,
            "skipped_count": self.skipped_count,
            "error_count": self.error_count,
            "errors": self.errors,
            "ratings_created": self.ratings_created,
            "departments_created": self.departments_created,
            "status": self.status,
        }


# --------------------------------------------------------------- dry run
def dry_run(
    db: Session, content: bytes, filename: str, *, scale_max: int
) -> DryRunResult:
    rows = read_rows(content, filename)
    if not rows:
        return DryRunResult(
            filename=filename,
            total_rows=0,
            resolution=ColumnResolution({}, {}, {}, []),
            samples=[],
            known_departments=[],
            unknown_departments=[],
            missing_fields=[],
            duplicate_rows=0,
            warnings=["The file has no rows."],
        )

    resolution = resolve_columns(list(rows[0].keys()))
    existing = _department_index(db)

    known = [name for name in resolution.department_ratings if _key(name) in existing]
    unknown = [name for name in resolution.department_ratings if _key(name) not in existing]

    missing = [
        field_name
        for field_name in ("submitted_at_source", "customer_name", "overall_rating")
        if field_name not in resolution.fields
    ]

    samples = [
        _parse_row(row, resolution, scale_max=scale_max) for row in rows[:SAMPLE_ROWS]
    ]

    hashes = [row_hash(row, resolution) for row in rows]
    duplicates = len(hashes) - len(set(hashes))

    warnings: list[str] = []
    if resolution.unmapped:
        warnings.append(
            f"{len(resolution.unmapped)} column(s) could not be placed and will be ignored."
        )
    if unknown:
        warnings.append(
            "New department(s) in this form: "
            + ", ".join(unknown)
            + ". They will be created on commit."
        )
    if duplicates:
        warnings.append(f"{duplicates} duplicate row(s) in the file itself.")
    if not resolution.department_ratings:
        warnings.append("No department rating columns were recognised.")

    return DryRunResult(
        filename=filename,
        total_rows=len(rows),
        resolution=resolution,
        samples=samples,
        known_departments=sorted(known),
        unknown_departments=sorted(unknown),
        missing_fields=missing,
        duplicate_rows=duplicates,
        warnings=warnings,
    )


def _key(name: str) -> str:
    return feedback_ingest.key(name)


def _department_index(db: Session) -> dict[str, Department]:
    return feedback_ingest.department_index(db)


def _parse_row(
    row: dict[str, str], resolution: ColumnResolution, *, scale_max: int
) -> dict:
    return feedback_ingest.parse_row(row, resolution, scale_max=scale_max)


# ---------------------------------------------------------------- commit
def commit(
    db: Session,
    content: bytes,
    filename: str,
    *,
    actor: User,
    scale_max: int,
    create_missing_departments: bool = True,
) -> CommitResult:
    """Write the batch. Caller commits the transaction."""
    rows = read_rows(content, filename)
    resolution = resolve_columns(list(rows[0].keys())) if rows else ColumnResolution({}, {}, {}, [])

    batch = FeedbackImport(
        filename=filename,
        source=FeedbackImportSource.GOOGLE_FORMS_XLSX,
        uploaded_by_user_id=actor.id,
        column_map=resolution.as_dict(),
    )
    db.add(batch)
    db.flush()

    departments = _department_index(db)
    created_departments: list[str] = []

    if create_missing_departments:
        next_order = (db.scalar(select(func.max(Department.sort_order))) or 0) + 10
        for name in resolution.department_ratings:
            if _key(name) in departments:
                continue
            department = Department(
                name=name,
                code=_department_code(name, departments),
                sort_order=next_order,
            )
            db.add(department)
            db.flush()
            departments[_key(name)] = department
            created_departments.append(name)
            next_order += 10

    users_by_name = {
        _key(user.name): user.id for user in db.execute(select(User)).scalars()
    }

    created = skipped = errors_count = ratings_created = 0
    errors: list[dict] = []
    seen: set[str] = set()
    code_header = feedback_requests.reference_header(list(rows[0].keys())) if rows else None

    for index, row in enumerate(rows, start=2):    # 2 = first row under the header
        digest = row_hash(row, resolution)
        if digest in seen:
            skipped += 1
            errors.append(
                {
                    "row": index,
                    "type": "skipped",
                    "reason": "duplicate_in_batch",
                    "message": "Same as an earlier row in this file.",
                }
            )
            continue
        seen.add(digest)

        if db.execute(
            select(Feedback.id).where(Feedback.source_row_hash == digest)
        ).first():
            skipped += 1
            errors.append(
                {
                    "row": index,
                    "type": "skipped",
                    "reason": "duplicate_in_db",
                    "message": "Already imported in an earlier batch.",
                }
            )
            continue

        parsed = _parse_row(row, resolution, scale_max=scale_max)
        if not feedback_ingest.has_any_rating(parsed):
            errors_count += 1
            errors.append(
                {
                    "row": index,
                    "type": "error",
                    "column": "ratings",
                    "message": "No rating on this row.",
                }
            )
            continue

        # The same matching the webhook uses. The importer used to skip the
        # request code entirely, so an exported response to a lead's ask never
        # completed that ask - and this is the ingestion path that works
        # without the webhook configured.
        code = (row.get(code_header) or "").strip() if code_header else ""
        request, matched = feedback_requests.match_response(
            db, code=code, mobile=parsed["mobile"], email=parsed["email"]
        )
        if request is not None and request.status == FeedbackRequestStatus.COMPLETED:
            matched = FeedbackMatchStatus.DUPLICATE

        result = feedback_ingest.create_feedback(
            db,
            parsed,
            resolution,
            row,
            source=FeedbackSource.GOOGLE_FORMS_IMPORT,
            source_row_hash=digest,
            import_id=batch.id,
            departments=departments,
            users_by_name=users_by_name,
            feedback_request_id=request.id if request is not None else None,
            match_status=matched or FeedbackMatchStatus.MATCHED_CONTACT,
            customer_id=request.customer_id if request is not None else None,
            link_customer=request is None,
        )
        if request is not None and matched != FeedbackMatchStatus.DUPLICATE:
            feedback_requests.mark_completed(db, request)
        created += 1
        ratings_created += result.ratings_created

    status_value = (
        ImportStatus.FAILED
        if errors_count and not created
        else ImportStatus.PARTIAL
        if errors_count
        else ImportStatus.SUCCESS
    )

    batch.total_rows = len(rows)
    batch.created_count = created
    batch.skipped_count = skipped
    batch.error_count = errors_count
    batch.errors = errors or None
    batch.status = status_value
    db.flush()

    audit.record(
        db,
        actor_id=actor.id,
        action=AuditAction.FEEDBACK_IMPORTED,
        entity_type=EntityType.IMPORT,
        entity_id=batch.id,
        after={
            "filename": filename,
            "created": created,
            "skipped": skipped,
            "errors": errors_count,
        },
    )

    return CommitResult(
        import_id=batch.id,
        filename=filename,
        total_rows=len(rows),
        created_count=created,
        skipped_count=skipped,
        error_count=errors_count,
        errors=errors,
        ratings_created=ratings_created,
        departments_created=created_departments,
        status=status_value,
    )


def _department_code(name: str, existing: dict[str, Department]) -> str:
    return feedback_ingest.department_code(name, existing)


def import_history(db: Session, limit: int = 20) -> Iterable[FeedbackImport]:
    return (
        db.execute(
            select(FeedbackImport).order_by(FeedbackImport.created_at.desc()).limit(limit)
        )
        .scalars()
        .all()
    )
