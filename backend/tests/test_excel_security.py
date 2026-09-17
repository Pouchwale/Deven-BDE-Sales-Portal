"""The SAP upload is a file from outside the server. Everything about it is
checked by content, bounded, and never trusted for a filesystem path."""
from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.api import admin as admin_api
from app.core.constants import AuditAction
from app.models.customer import Customer
from app.models.system import AuditEvent
from app.services import customer_source, sap_sync
from tests.conftest import sign_in, super_admin_headers

UPLOAD = "/api/admin/sap-import/upload"
HEADER = "Customer Code,Customer Name,Mobile,Email,FGPO Code,Invoice Date,Sales Person\n"
ROW = "C7001,SECURE FOODS LLP,9000000071,secure@example.com,FGPO7001,2026-09-01,Kevin\n"


def upload(client, headers, name: str, content: bytes | str, mime: str = "text/csv"):
    if isinstance(content, str):
        content = content.encode("utf-8")
    return client.post(
        UPLOAD, headers=headers, files={"file": (name, io.BytesIO(content), mime)}
    )


def workbook_bytes(rows: list[list[object]], *, macro: bool = False) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    if not macro:
        return buffer.getvalue()
    with zipfile.ZipFile(buffer, "a") as archive:
        # What a macro-enabled workbook carries. openpyxl never runs it, and
        # keep_vba=False means it is not even kept in memory.
        archive.writestr("xl/vbaProject.bin", b"Attribute VB_Name = \"AutoOpen\"\nShell \"calc\"")
    return buffer.getvalue()


def error_message(response) -> str:
    return response.json()["error"]["message"]


# ------------------------------------------------------------- file type
@pytest.mark.parametrize("name", ["notes.txt", "old.xls", "script.xlsm.exe", "noext"])
def test_only_xlsx_xlsm_and_csv_are_accepted(client, name) -> None:
    response = upload(client, super_admin_headers(client), name, HEADER + ROW)
    assert response.status_code == 422, response.text


def test_a_text_file_renamed_to_xlsx_is_refused(client, db) -> None:
    response = upload(
        client,
        super_admin_headers(client),
        "export.xlsx",
        HEADER + ROW,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert response.status_code == 422
    assert "not a real Excel workbook" in error_message(response)
    assert db.execute(select(Customer).where(Customer.sap_code == "C7001")).first() is None


def test_a_zip_that_is_not_a_workbook_is_refused(client) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hello")
    response = upload(client, super_admin_headers(client), "export.xlsx", buffer.getvalue())
    assert response.status_code == 422


def test_a_binary_file_named_csv_is_refused(client) -> None:
    response = upload(
        client, super_admin_headers(client), "export.csv", b"Customer\x00Code\x00\x01\x02"
    )
    assert response.status_code == 422
    assert "not plain text" in error_message(response)


# ------------------------------------------------------------- size limits
def test_an_oversize_upload_is_refused_with_413(client, monkeypatch) -> None:
    monkeypatch.setattr(admin_api, "MAX_SAP_UPLOAD_BYTES", 2048)
    body = HEADER + ROW * 100   # ~8KB
    response = upload(client, super_admin_headers(client), "big.csv", body)
    assert response.status_code == 413, response.text


def test_the_file_size_limit_also_applies_to_the_reader(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(customer_source, "MAX_FILE_BYTES", 100)
    path = tmp_path / "big.csv"
    path.write_text(HEADER + ROW * 10, encoding="utf-8")
    with pytest.raises(customer_source.FileTooLarge):
        list(customer_source.FileCustomerSource(path).rows())


def test_a_zip_bomb_is_refused_before_it_is_expanded(client) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        chunk = b"\0" * (1024 * 1024)
        with archive.open("xl/worksheets/sheet1.xml", "w") as entry:
            for _ in range(customer_source.MAX_UNCOMPRESSED_BYTES // len(chunk) + 1):
                entry.write(chunk)
    content = buffer.getvalue()
    assert len(content) < customer_source.MAX_FILE_BYTES   # small on the wire

    response = upload(client, super_admin_headers(client), "bomb.xlsx", content)
    assert response.status_code == 422
    assert "expands to more than" in error_message(response)


def test_a_zip_with_too_many_parts_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(customer_source, "MAX_ZIP_ENTRIES", 5)
    path = tmp_path / "parts.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
        for index in range(10):
            archive.writestr(f"xl/part{index}.xml", "<x/>")
    with pytest.raises(customer_source.SourceFileRejected):
        customer_source.check_file(path)


# ------------------------------------------------------------- structure
def test_missing_columns_are_listed_in_the_422(client) -> None:
    response = upload(
        client, super_admin_headers(client), "wrong.csv", "Customer,Mobile\nC1,9000000000\n"
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "UNMAPPED_COLUMNS"
    assert "Customer Name" in body["details"]["missing_columns"]
    assert "Invoice No (or FGPO Code)" in body["details"]["missing_columns"]


def test_a_header_only_file_with_wrong_columns_is_refused(client) -> None:
    response = upload(client, super_admin_headers(client), "empty.csv", "Foo,Bar\n")
    assert response.status_code == 422


def test_too_many_rows_is_refused(client, db, monkeypatch) -> None:
    monkeypatch.setattr(customer_source, "MAX_ROWS", 3)
    body = HEADER + "".join(
        ROW.replace("C7001", f"C70{index:02d}") for index in range(10, 15)
    )
    response = upload(client, super_admin_headers(client), "many.csv", body)
    assert response.status_code == 422
    assert "more than" in error_message(response)
    assert db.execute(select(Customer).where(Customer.sap_code == "C7010")).first() is None


def test_bad_rows_are_reported_with_row_numbers(client) -> None:
    body = HEADER + ROW + "C7002,BAD DATE LLP,9000000072,,FGPO7002,not a date,Kevin\n"
    response = upload(client, super_admin_headers(client), "rows.csv", body)
    assert response.status_code == 200, response.text
    result = response.json()["files"][0]
    assert result["error_count"] == 1
    assert {"row": 3, "customer": "C7002"}.items() <= result["errors"][0].items()


# ------------------------------------------------------------- macros
def test_a_macro_workbook_imports_its_data_and_runs_nothing(client, db) -> None:
    content = workbook_bytes(
        [
            ["Customer Code", "Customer Name", "Mobile", "Email", "FGPO Code",
             "Invoice Date", "Sales Person"],
            # A formula with no cached value: evaluated it would be an
            # address; read as data it is empty. Never the formula text.
            ["C7003", "MACRO FOODS LLP", "9000000073", '=CONCAT("x","@example.com")',
             "FGPO7003", "2026-09-01", "Kevin"],
        ],
        macro=True,
    )
    response = upload(
        client,
        super_admin_headers(client),
        "live.xlsm",
        content,
        "application/vnd.ms-excel.sheet.macroEnabled.12",
    )
    assert response.status_code == 200, response.text
    assert response.json()["files"][0]["customers_created"] == 1

    customer = db.execute(select(Customer).where(Customer.sap_code == "C7003")).scalar_one()
    assert customer.name == "MACRO FOODS LLP"
    assert customer.email is None


# ------------------------------------------------------------- filenames and temp files
@pytest.mark.parametrize(
    "hostile, expected",
    [
        ("../../../../etc/passwd.csv", "passwd.csv"),
        ("..\\..\\Windows\\System32\\drivers.csv", "drivers.csv"),
        ("C:\\Users\\victim\\export.csv", "export.csv"),
        ("file:///etc/export.csv", "export.csv"),
    ],
)
def test_a_path_in_the_filename_is_reduced_to_a_basename(
    client, monkeypatch, hostile, expected
) -> None:
    created: list[str] = []
    original = tempfile.mkstemp

    def spy(*args, **kwargs):
        handle, name = original(*args, **kwargs)
        created.append(name)
        return handle, name

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    response = upload(client, super_admin_headers(client), hostile, HEADER + ROW)
    assert response.status_code == 200, response.text
    assert response.json()["files"][0]["filename"] == expected
    # The bytes went to a random private temp file, never a path built from
    # the client's name - and it is gone afterwards.
    assert len(created) == 1
    assert Path(created[0]).name.startswith(customer_source.TEMP_PREFIX)
    assert expected not in Path(created[0]).name
    assert not Path(created[0]).exists()


def test_the_temp_file_is_deleted_when_the_import_fails(client, monkeypatch) -> None:
    created: list[str] = []
    original = tempfile.mkstemp

    def spy(*args, **kwargs):
        handle, name = original(*args, **kwargs)
        created.append(name)
        return handle, name

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    response = upload(client, super_admin_headers(client), "fake.xlsx", "not a workbook")
    assert response.status_code == 422
    assert created and not Path(created[0]).exists()


def test_safe_display_name() -> None:
    assert customer_source.safe_display_name("<script>.csv") == "_script_.csv"
    assert customer_source.safe_display_name("") == "upload"
    assert customer_source.safe_display_name("a" * 300 + ".xlsx").endswith(".xlsx")
    assert len(customer_source.safe_display_name("a" * 300 + ".xlsx")) <= 120


def test_the_import_button_only_ever_imports_the_configured_file(client) -> None:
    response = client.post(
        "/api/admin/sap-import",
        headers=super_admin_headers(client),
        params={"path": "C:/Windows/win.ini", "file": "../../secret.csv", "url": "file:///etc"},
    )
    assert response.status_code == 200, response.text
    assert [f["filename"] for f in response.json()["files"]] == ["sap_invoices_real.csv"]


# ------------------------------------------------------------- status leaks no path
def test_status_carries_the_file_name_never_its_path(client, tmp_path, monkeypatch) -> None:
    missing = tmp_path / "secret folder" / "SAP DATA.xlsm"
    monkeypatch.setattr(sap_sync.settings, "SAP_DATA_FILE", str(missing))
    monkeypatch.setattr(sap_sync, "_state", dict(sap_sync._state, last_error=None))

    assert sap_sync.check_once() is False     # records "not found"
    response = client.get("/api/admin/sap-sync/status", headers=super_admin_headers(client))
    assert response.status_code == 200
    body = response.json()
    assert "linked_file" not in body
    assert body["linked"] is True
    assert body["linked_file_name"] == "SAP DATA.xlsm"
    assert body["file_found"] is False
    assert body["last_error"]
    assert str(tmp_path) not in response.text
    assert "secret folder" not in response.text


def test_an_import_of_a_missing_linked_file_has_no_path_in_the_error(
    client, tmp_path, monkeypatch
) -> None:
    missing = tmp_path / "hidden dir" / "SAP.xlsm"
    monkeypatch.setattr(sap_sync.settings, "SAP_DATA_FILE", str(missing))
    monkeypatch.setattr(sap_sync, "_state", dict(sap_sync._state, last_error=None))
    response = client.post("/api/admin/sap-import", headers=super_admin_headers(client))
    assert response.status_code == 422
    assert "hidden dir" not in response.text and str(tmp_path) not in response.text


def test_scrub_paths_removes_windows_and_posix_paths() -> None:
    message = (
        "[Errno 13] Permission denied: 'C:\\Users\\deven\\OneDrive\\Desktop\\x.xlsm' "
        "and /home/app/data/sap.csv"
    )
    scrubbed = customer_source.scrub_paths(message)
    assert "deven" not in scrubbed and "/home/app" not in scrubbed


# ------------------------------------------------------------- who, how often, audit
def test_only_the_super_admin_can_import(client, users) -> None:
    headers = sign_in(client, users["Shail Patel"])
    assert client.get("/api/admin/sap-sync/status", headers=headers).status_code == 403
    assert client.post("/api/admin/sap-import", headers=headers).status_code == 403
    assert upload(client, headers, "x.csv", HEADER + ROW).status_code == 403


def test_imports_are_rate_limited_per_user(client) -> None:
    headers = super_admin_headers(client)
    for _ in range(admin_api.SAP_IMPORT_RATE_LIMIT):
        # Refused files still count: each one was work for the server.
        assert upload(client, headers, "x.txt", "nope").status_code == 422
    response = upload(client, headers, "x.csv", HEADER + ROW)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert client.post("/api/admin/sap-import", headers=headers).status_code == 429


def test_a_successful_upload_is_audited_with_counts(client, db) -> None:
    response = upload(client, super_admin_headers(client), "audited.csv", HEADER + ROW)
    assert response.status_code == 200
    event = db.execute(
        select(AuditEvent)
        .where(AuditEvent.action == str(AuditAction.SAP_IMPORTED))
        .order_by(AuditEvent.created_at.desc())
    ).scalars().first()
    assert event is not None and event.actor_user_id is not None
    assert event.after["filename"] == "audited.csv"
    assert event.after["result"] == "SUCCESS"
    assert event.after["customers_created"] == 1


def test_a_refused_upload_is_audited_as_rejected(client, db) -> None:
    response = upload(client, super_admin_headers(client), "fake.xlsx", "not a workbook")
    assert response.status_code == 422
    events = db.execute(
        select(AuditEvent).where(AuditEvent.action == str(AuditAction.SAP_IMPORTED))
    ).scalars().all()
    rejected = [e for e in events if (e.after or {}).get("result") == "REJECTED"]
    assert rejected and rejected[-1].after["filename"] == "fake.xlsx"
