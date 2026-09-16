"""The boundary between the company's post-sale sheet and the portal.

Three routes, and the shape is deliberate:

    POST /post-sale/sync        bring rows in
    GET  /post-sale/status      has anything arrived, and how did it go
    GET  /post-sale/unmatched   rows that need a human
    POST /post-sale/resolve/…   a human says which lead a row belongs to

Administrators only. Reconciling an unmatched row means deciding which
customer a sales team is about to phone, and an unmatched row has no lead yet,
so there is nobody it could be scoped to.

The field NAMES the sheet uses are the company's business, not ours: `sync`
takes the portal's vocabulary and whoever feeds it maps the sheet's columns
onto that. Inventing a schema now, before the file exists, would just be a
guess everyone had to work around later.
"""
from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field

from fastapi import APIRouter

from app.core.deps import AdminUser, DbSession
from app.core.errors import not_found
from app.core.validators import Phone
from app.schemas.common import Message
from app.services import post_sale as post_sale_service

router = APIRouter(prefix="/post-sale", tags=["post-sale"])


class PostSaleRow(BaseModel):
    """One row of the sheet, in the portal's vocabulary.

    `invoice_date` is the field the ten-day eligibility rule runs from. It is
    named for the business meaning rather than for whatever the sheet calls
    its column, so the rule does not have to change when the sheet does.
    """

    external_ref: str | None = Field(default=None, max_length=128)
    customer_name: str | None = Field(default=None, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    mobile: Phone = Field(default=None)
    email: str | None = Field(default=None, max_length=255)
    invoice_date: date | None = None
    notes: str | None = None


class SyncBody(BaseModel):
    rows: list[PostSaleRow] = Field(min_length=1, max_length=1000)


class ResolveBody(BaseModel):
    lead_id: uuid.UUID


@router.post("/sync")
def sync(payload: SyncBody, _: AdminUser, db: DbSession) -> dict:
    """Bring a batch of post-sale rows in.

    Idempotent per `external_ref`, so a nightly sync can run twice without
    stacking duplicates. Rows that cannot be matched to a converted lead are
    stored as UNMATCHED rather than attached to a best guess.
    """
    result = post_sale_service.sync_rows(
        db, [row.model_dump() for row in payload.rows]
    )
    db.commit()
    return result.as_dict()


@router.get("/status")
def status(_: AdminUser, db: DbSession) -> dict:
    """Whether anything has been synced, and how much of it landed.

    `ever_synced` is what lets the modules say "waiting for the next sync"
    instead of showing a zero that reads as "there are no customers".
    """
    return post_sale_service.status_summary(db)


@router.get("/unmatched")
def unmatched(_: AdminUser, db: DbSession) -> list[dict]:
    """Rows the portal refused to guess about."""
    return [
        {
            "id": str(record.id),
            "external_ref": record.external_ref,
            "customer_name": record.customer_name,
            "company_name": record.company_name,
            "mobile": record.mobile,
            "email": record.email,
            "invoice_date": record.invoice_date,
            "status": record.status,
            "synced_at": record.synced_at,
        }
        for record in post_sale_service.unmatched(db)
    ]


@router.post("/resolve/{record_id}", response_model=Message)
def resolve(
    record_id: uuid.UUID, payload: ResolveBody, _: AdminUser, db: DbSession
) -> Message:
    """Attach an unmatched row to the lead a person identified."""
    record = post_sale_service.resolve(db, record_id, payload.lead_id)
    if record is None:
        raise not_found("No such post-sale record, or that lead is not converted.")
    db.commit()
    return Message(message=f"Filed under {record.lead.name}.")
