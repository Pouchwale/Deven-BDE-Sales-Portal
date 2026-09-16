"""The Super Admin "Import SAP data" button."""
from __future__ import annotations

from tests.conftest import sign_in, super_admin_headers


def test_super_admin_can_run_the_sap_import(client) -> None:
    response = client.post("/api/admin/sap-import", headers=super_admin_headers(client))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["files"][0]["filename"] == "sap_invoices_real.csv"
    # Already imported by the seed, so a re-run creates nothing.
    assert body["files"][0]["lines_created"] == 0
    assert body["files"][0]["lines_skipped"] == 30


def test_super_admin_can_upload_a_sap_file(client) -> None:
    upload = (
        "Customer Code,Customer Name,Mobile,Email,FGPO Code,Invoice Date,Sales Person\n"
        "C9990,UPLOADED ACCOUNT LLP,9000000001,up@example.com,FGPO9990,2026-09-01,Kevin\n"
    )
    response = client.post(
        "/api/admin/sap-import/upload",
        headers=super_admin_headers(client),
        files={"file": ("manual.csv", upload, "text/csv")},
    )
    assert response.status_code == 200, response.text
    result = response.json()["files"][0]
    assert result["filename"] == "manual.csv"
    assert result["customers_created"] == 1 and result["lines_created"] == 1


def test_sap_customers_become_converted_leads_for_their_sales_person(db, tmp_path) -> None:
    from datetime import date

    from sqlalchemy import select

    from app.core.constants import LeadStatus
    from app.models.lead import Lead
    from app.models.org import User
    from app.models.post_sale import PostSaleRecord
    from app.services import metrics
    from app.services.customer_source import FileCustomerSource
    from app.services.sap_import import import_source

    export = tmp_path / "sap.csv"
    export.write_text(
        "Customer Code,Customer Name,Mobile,Email,FGPO Code,Invoice Date,Sales Person\n"
        "C9980,LINKED FOODS LLP,9000000080,linked@example.com,FGPO9980,2026-01-05,Kevin\n",
        encoding="utf-8",
    )
    first = import_source(db, FileCustomerSource(export), link_leads=True)
    assert first.leads_created == 1

    record = db.execute(
        select(PostSaleRecord).where(PostSaleRecord.external_ref == "SAP:C9980")
    ).scalar_one()
    lead = db.get(Lead, record.lead_id)
    kevin = db.execute(select(User).where(User.name == "Kevin")).scalar_one()
    assert lead.status == LeadStatus.CONVERTED
    assert lead.assigned_to_user_id == kevin.id
    assert record.status == "MATCHED" and record.invoice_date == date(2026, 1, 5)
    # Invoiced long ago, so it is askable for a reference and feedback.
    assert metrics.eligibility_invoice_dates(db, [lead.id]) == {lead.id: date(2026, 1, 5)}

    again = import_source(db, FileCustomerSource(export), link_leads=True)
    assert again.leads_created == 0
    assert db.execute(
        select(PostSaleRecord).where(PostSaleRecord.external_ref == "SAP:C9980")
    ).scalar_one().lead_id == lead.id


def test_an_upload_that_is_not_a_spreadsheet_is_refused(client) -> None:
    response = client.post(
        "/api/admin/sap-import/upload",
        headers=super_admin_headers(client),
        files={"file": ("notes.txt", "hello", "text/plain")},
    )
    assert response.status_code == 422


def test_an_admin_cannot_run_the_sap_import(client, users) -> None:
    response = client.post(
        "/api/admin/sap-import", headers=sign_in(client, users["Shail Patel"])
    )
    assert response.status_code == 403
