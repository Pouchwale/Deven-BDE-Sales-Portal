"""The office PC pushing the SAP workbook (POST /api/admin/sap-import/push)."""
from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from app.api import sap_push
from app.core.config import settings
from app.models.post_sale import PostSaleRecord
from app.models.system import AuditEvent

PUSH = "/api/admin/sap-import/push"
SECRET = "push-secret-for-tests-only-0123456789abcdef"

EXPORT = (
    "Customer Code,Customer Name,Mobile,Email,FGPO Code,Invoice Date,Sales Person\n"
    "C9970,PUSHED FOODS LLP,9000000070,pushed@example.com,FGPO9970,2026-09-01,Kevin\n"
).encode()


@pytest.fixture
def push_on(monkeypatch):
    monkeypatch.setattr(settings, "SAP_PUSH_SECRET", SECRET, raising=False)
    # Each test starts with an empty limiter.
    sap_push.push_limiter._hits.clear()
    yield


def _push(client, body: bytes = EXPORT, *, secret: str = SECRET, timestamp: int | None = None,
          filename: str = "BDE & SALES PORTAL SAP DATA.csv", signature: str | None = None):
    headers = {
        "Content-Type": "application/octet-stream",
        sap_push.FILENAME_HEADER: filename,
    }
    headers[sap_push.SIGNATURE_HEADER] = signature if signature is not None else sap_push.sign(body, secret, timestamp)
    return client.post(PUSH, content=body, headers=headers)


def test_the_route_is_off_without_a_secret(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "SAP_PUSH_SECRET", "", raising=False)
    response = _push(client)
    assert response.status_code == 503


def test_an_unsigned_push_is_refused(client, db, push_on) -> None:
    response = client.post(PUSH, content=EXPORT, headers={sap_push.FILENAME_HEADER: "sap.csv"})
    assert response.status_code == 401
    assert db.execute(select(PostSaleRecord).where(PostSaleRecord.external_ref == "SAP:C9970")).first() is None


def test_the_wrong_secret_is_refused(client, push_on) -> None:
    assert _push(client, secret="not-the-secret-at-all-0123456789abcdef").status_code == 401


def test_a_tampered_body_is_refused(client, push_on) -> None:
    signature = sap_push.sign(EXPORT, SECRET)
    tampered = EXPORT.replace(b"PUSHED", b"FORGED")
    assert _push(client, tampered, signature=signature).status_code == 401


def test_a_replay_outside_the_window_is_refused(client, push_on) -> None:
    stale = int(time.time()) - settings.SAP_PUSH_MAX_SKEW_SECONDS - 60
    assert _push(client, timestamp=stale).status_code == 401


def test_a_signed_push_runs_the_import_and_is_idempotent(client, db, push_on, monkeypatch) -> None:
    # As on the live server: each SAP customer becomes a converted lead.
    monkeypatch.setattr(settings, "SAP_LINK_LEADS", True, raising=False)
    first = _push(client)
    assert first.status_code == 200, first.text
    result = first.json()["files"][0]
    assert result["customers_created"] == 1 and result["lines_created"] == 1

    record = db.execute(select(PostSaleRecord).where(PostSaleRecord.external_ref == "SAP:C9970")).scalar_one()
    assert record.lead_id is not None  # became a converted lead, as the button does

    # The uploader may send the same workbook again: nothing new is created.
    again = _push(client).json()["files"][0]
    assert again["customers_created"] == 0 and again["lines_created"] == 0

    audited = db.execute(select(AuditEvent).where(AuditEvent.action == "SAP_IMPORTED")).scalars().all()
    assert audited, "the push is on the audit trail"


def test_a_file_that_is_not_a_sap_export_is_refused(client, push_on) -> None:
    response = _push(client, b"hello", filename="notes.txt")
    assert response.status_code == 422


def test_an_oversized_push_is_refused_before_the_signature_is_checked(client, push_on, monkeypatch) -> None:
    from app.services import customer_source

    monkeypatch.setattr(customer_source, "MAX_FILE_BYTES", 10)
    response = _push(client)
    assert response.status_code == 413


def test_the_office_pc_uploader_signs_what_the_server_accepts(client, push_on) -> None:
    """The client (deploy/sync/sap_uploader.py) and this route must agree."""
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "deploy" / "sync" / "sap_uploader.py"
    spec = importlib.util.spec_from_file_location("sap_uploader_under_test", script)
    uploader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(uploader)

    response = _push(client, signature=uploader.sign(EXPORT, SECRET))
    assert response.status_code == 200, response.text
