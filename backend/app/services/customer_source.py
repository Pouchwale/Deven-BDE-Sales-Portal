"""Where converted customers come from.

Dev and demo read the SAP export file. Production will query SAP B1 directly.
The service layer talks to this Protocol and never to pandas or to psycopg,
so swapping the source touches this file plus configuration and nothing else.

COLUMN_MAP is deliberately reusable as the SELECT alias list for the future
SQL implementation, which is why the keys are the SAP column captions.
"""
from __future__ import annotations

import csv
import io
import os
import re
import shutil
import tempfile
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Protocol

from app.core import text

# ------------------------------------------------------------ file safety
#: The only file types the importer opens. `.xls` (BIFF) is refused: nothing
#: installed can read it, and a clear "save as .xlsx" beats a stack trace.
ALLOWED_SUFFIXES: tuple[str, ...] = (".xlsx", ".xlsm", ".csv")
EXCEL_SUFFIXES: frozenset[str] = frozenset({".xlsx", ".xlsm"})
#: Largest file accepted, compressed. The real workbook is a few tens of KB.
MAX_FILE_BYTES = 10 * 1024 * 1024
#: A workbook is a ZIP; this caps what it may expand to (zip-bomb guard).
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
#: A real workbook has a few dozen parts.
MAX_ZIP_ENTRIES = 2_000
#: Data rows per file. The live sheet holds hundreds.
MAX_ROWS = 50_000
ZIP_MAGIC = b"PK\x03\x04"

#: Captions shown when a required column is missing.
FIELD_CAPTIONS: dict[str, str] = {
    "sap_code": "Customer Code",
    "name": "Customer Name",
    "invoice_no": "Invoice No (or FGPO Code)",
}


class SourceFileRejected(ValueError):
    """The file is not one the importer will open. The message is safe to
    show a user: it never contains a server path."""

    status_code = 422


class FileTooLarge(SourceFileRejected):
    status_code = 413


class TooManyRows(SourceFileRejected):
    pass


def safe_display_name(filename: str | None, *, default: str = "upload") -> str:
    """A client-supplied filename reduced to a harmless basename.

    Used ONLY for display and the audit trail - never to build a path.
    Directory parts (either slash), control characters and anything outside a
    conservative character set are removed, and the result is length-capped.
    """
    raw = (filename or "").replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[^\w .()&+,\-]", "_", raw).strip(" .")
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    if len(cleaned) > 120:
        stem, dot, suffix = cleaned.rpartition(".")
        cleaned = (stem[: 110] + dot + suffix[:8]) if dot else cleaned[:120]
    return cleaned or default


_WINDOWS_PATH = re.compile(r"(?:\b[A-Za-z]:[\\/]|\\\\)[^\s'\"]*")
_POSIX_PATH = re.compile(r"(?<![\w.])/(?:[^\s/'\"]+/)+[^\s/'\"]*")


def scrub_paths(message: str, *known: str | Path | None) -> str:
    """Remove filesystem paths from a message before it leaves the server."""
    out = message
    for value in sorted({str(k) for k in known if k}, key=len, reverse=True):
        name = Path(value).name
        out = out.replace(value, name or "file")
    out = _WINDOWS_PATH.sub("<path>", out)
    return _POSIX_PATH.sub("<path>", out)


def check_file(path: Path, suffix: str | None = None) -> str:
    """Refuse anything that is not plausibly the SAP export. Returns the suffix.

    Checks the CONTENT, not the name: an .xlsx must be a ZIP whose expanded
    size and part count are bounded; a .csv must be text with no NUL bytes.
    """
    suffix = (suffix or path.suffix).lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise SourceFileRejected("Choose an Excel (.xlsx / .xlsm) or .csv file.")
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise FileTooLarge(
            f"That file is larger than {MAX_FILE_BYTES // (1024 * 1024)}MB."
        )
    with path.open("rb") as handle:
        head = handle.read(8192)
    _check_content(suffix, size, head, lambda: zipfile.ZipFile(path))
    return suffix


def check_bytes(content: bytes, suffix: str) -> str:
    """`check_file` for an upload already held in memory."""
    suffix = suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise SourceFileRejected("Choose an Excel (.xlsx / .xlsm) or .csv file.")
    if len(content) > MAX_FILE_BYTES:
        raise FileTooLarge(
            f"That file is larger than {MAX_FILE_BYTES // (1024 * 1024)}MB."
        )
    _check_content(
        suffix, len(content), content[:8192], lambda: zipfile.ZipFile(io.BytesIO(content))
    )
    return suffix


def _check_content(suffix: str, size: int, head: bytes, open_zip) -> None:
    if size == 0:
        raise SourceFileRejected("That file is empty.")

    if suffix in EXCEL_SUFFIXES:
        if not head.startswith(ZIP_MAGIC):
            raise SourceFileRejected(
                "That file is not a real Excel workbook (.xlsx / .xlsm)."
            )
        try:
            with open_zip() as archive:
                infos = archive.infolist()
        except (zipfile.BadZipFile, OSError) as exc:
            if isinstance(exc, PermissionError):
                raise
            raise SourceFileRejected(
                "That workbook is damaged or only partly saved."
            ) from None
        if len(infos) > MAX_ZIP_ENTRIES:
            raise SourceFileRejected("That workbook has too many parts to be an export.")
        if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
            raise SourceFileRejected(
                "That workbook expands to more than "
                f"{MAX_UNCOMPRESSED_BYTES // (1024 * 1024)}MB and was refused."
            )
        names = {info.filename for info in infos}
        if "xl/workbook.xml" not in names:
            raise SourceFileRejected(
                "That file is not a real Excel workbook (.xlsx / .xlsm)."
            )
    else:
        if b"\x00" in head or head.startswith(ZIP_MAGIC):
            raise SourceFileRejected("That .csv file is not plain text.")

# SAP caption (normalised) -> our field name.
COLUMN_MAP: dict[str, str] = {
    "customer": "sap_code",
    "customer code": "sap_code",
    "customer name": "name",
    "mobile": "mobile",
    "email": "email",
    "fgpo code": "fgpo_code",
    "item description": "item_description",
    "invoice date": "invoice_date",
    # The workbook publishes the day an account may be asked. Read, never
    # recomputed - see core/eligibility.py.
    "reference date": "reference_date",
    "invoice no": "invoice_no",
    "sales person": "sales_person",
}

# Without these a row cannot be attached to a customer or de-duplicated.
REQUIRED_FIELDS = ("sap_code", "name", "invoice_no")

# The formats seen in the real export and in the SAP UI's own display. The
# timestamp form is how a real Excel date cell reads once loaded as text.
_DATE_FORMATS = (
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S"
)

_WHITESPACE = re.compile(r"\s+")


#: `  Invoice  No ` -> `invoice no`. One normaliser for every importer.
normalise_header = text.normalise_header


def parse_date(value: str | None) -> date | None:
    """Parse a SAP date, or return None. Never guesses between d/m and m/d.

    Every format tried is day-first or ISO, because the export is Indian. A
    silent m/d reading would move June invoices into December.
    """
    text = (value or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


@dataclass(frozen=True, slots=True)
class CustomerRow:
    """One SAP invoice line, already typed. The unit both sources yield."""

    sap_code: str
    name: str
    mobile: str | None
    email: str | None
    fgpo_code: str | None
    item_description: str | None
    invoice_no: str
    invoice_date: date | None
    #: The sheet's Reference Date, when it has that column.
    reference_date: date | None
    sales_person: str | None
    # The raw source cells, kept for the idempotency hash and for error
    # reporting that quotes what was actually in the file.
    raw: dict[str, str]


class CustomerSource(Protocol):
    """Anything that can yield SAP invoice lines."""

    @property
    def name(self) -> str: ...

    def rows(self) -> Iterable[CustomerRow]: ...


class UnmappedColumns(ValueError):
    def __init__(self, missing: list[str]) -> None:
        super().__init__(
            "The file is missing required SAP columns: " + ", ".join(missing)
        )
        self.missing = missing
        self.captions = [FIELD_CAPTIONS.get(field, field) for field in missing]


#: Prefix of the private temp copies the importer makes; deleted after use.
TEMP_PREFIX = "bde_sap_"


class FileCustomerSource:
    """Reads the SAP CSV/XLSX export.

    Rows are yielded exactly as the file has them - no cleaning, no
    normalising of names or numbers. The file is the record of what SAP said.

    Never executes anything in the file: workbooks are opened by openpyxl in
    read-only, cached-values mode (`data_only`), with VBA discarded, so
    formulas are not evaluated and macros are never run.
    """

    def __init__(self, path: str | Path, *, display_name: str | None = None) -> None:
        self.path = Path(path)
        self._display_name = display_name

    @property
    def name(self) -> str:
        return self._display_name or self.path.name

    def _raw_rows(self) -> tuple[list[str], list[dict[str, str]]]:
        suffix = self.path.suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise SourceFileRejected("Choose an Excel (.xlsx / .xlsm) or .csv file.")
        try:
            return self._read(self.path, suffix)
        except PermissionError:
            if suffix not in EXCEL_SUFFIXES:
                raise
            # Excel holds an exclusive lock on a workbook it has open, but
            # Windows still lets it be copied. Read the last saved version
            # from a private temp copy (never named after the source), and
            # always delete it.
            handle, copy = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=suffix)
            os.close(handle)
            try:
                shutil.copy2(self.path, copy)
                return self._read(Path(copy), suffix)
            finally:
                with suppress(OSError):
                    os.unlink(copy)

    @staticmethod
    def _read(path: Path, suffix: str) -> tuple[list[str], list[dict[str, str]]]:
        check_file(path, suffix)
        if suffix in EXCEL_SUFFIXES:
            import pandas as pd

            try:
                frame = pd.read_excel(
                    path,
                    sheet_name=0,
                    dtype=str,
                    keep_default_na=False,
                    engine="openpyxl",
                    # pandas already opens read_only + data_only; keep_vba is
                    # stated so a future default change cannot turn it on.
                    engine_kwargs={"read_only": True, "data_only": True, "keep_vba": False},
                )
            except PermissionError:
                raise
            except OSError:
                raise SourceFileRejected("Could not read that workbook.") from None
            except Exception:  # noqa: BLE001 - openpyxl raises many types
                raise SourceFileRejected(
                    "Could not read that workbook. Save it again as .xlsx and retry."
                ) from None
            if len(frame.index) > MAX_ROWS:
                raise TooManyRows(f"That file has more than {MAX_ROWS:,} rows.")
            headers = [str(column) for column in frame.columns]
            rows = [
                {str(k): ("" if v is None else str(v)) for k, v in row.items()}
                for row in frame.to_dict(orient="records")
            ]
            return headers, rows

        # utf-8-sig strips the BOM Excel writes when it saves a CSV.
        try:
            content = path.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise SourceFileRejected(
                "That .csv file is not UTF-8 text. Save it as CSV UTF-8 and retry."
            ) from None
        if "\x00" in content:
            raise SourceFileRejected("That .csv file is not plain text.")
        reader = csv.DictReader(io.StringIO(content, newline=""))
        rows: list[dict[str, str]] = []
        try:
            for row in reader:
                if len(rows) >= MAX_ROWS:
                    raise TooManyRows(f"That file has more than {MAX_ROWS:,} rows.")
                rows.append(dict(row))
        except csv.Error:
            raise SourceFileRejected("That .csv file could not be parsed.") from None
        return list(reader.fieldnames or []), rows

    def resolve_columns(self, headers: Iterable[str]) -> dict[str, str]:
        """Map the file's headers onto our fields, or raise.

        Returns {our field: original header}, which is what gets stored on the
        import row so a later "why did this import look wrong?" is answerable.
        """
        resolved: dict[str, str] = {}
        for header in headers:
            field = COLUMN_MAP.get(normalise_header(header))
            if field and field not in resolved:
                resolved[field] = header
        # The BDE & Sales workbook carries one FGPO per customer and no invoice
        # number. The FGPO is the only document reference it has, so it stands
        # in as the invoice identifier.
        if "invoice_no" not in resolved and "fgpo_code" in resolved:
            resolved["invoice_no"] = resolved["fgpo_code"]
        missing = [f for f in REQUIRED_FIELDS if f not in resolved]
        if missing:
            raise UnmappedColumns(missing)
        return resolved

    def rows(self) -> Iterable[CustomerRow]:
        headers, raw_rows = self._raw_rows()
        # Checked even for a header-only file, so a sheet with the wrong
        # columns is refused rather than imported as "nothing to do".
        mapping = self.resolve_columns(headers)
        if not raw_rows:
            return

        for raw in raw_rows:
            def cell(field: str) -> str:
                header = mapping.get(field)
                return (raw.get(header, "") if header else "").strip()

            if not cell("sap_code") and not cell("invoice_no"):
                continue                      # a blank spacer row

            yield CustomerRow(
                sap_code=cell("sap_code"),
                name=cell("name"),
                mobile=cell("mobile") or None,
                email=cell("email") or None,
                fgpo_code=cell("fgpo_code") or None,
                item_description=cell("item_description") or None,
                invoice_no=cell("invoice_no"),
                invoice_date=parse_date(cell("invoice_date")),
                reference_date=parse_date(cell("reference_date")),
                sales_person=cell("sales_person") or None,
                raw={k: (v or "") for k, v in raw.items()},
            )
