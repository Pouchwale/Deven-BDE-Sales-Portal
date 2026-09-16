"""Where converted customers come from.

Dev and demo read the SAP export file. Production will query SAP B1 directly.
The service layer talks to this Protocol and never to pandas or to psycopg,
so swapping the source touches this file plus configuration and nothing else.

COLUMN_MAP is deliberately reusable as the SELECT alias list for the future
SQL implementation, which is why the keys are the SAP column captions.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Protocol

from app.core import text

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


class FileCustomerSource:
    """Reads the SAP CSV/XLSX export.

    Rows are yielded exactly as the file has them - no cleaning, no
    normalising of names or numbers. The file is the record of what SAP said.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @property
    def name(self) -> str:
        return self.path.name

    def _raw_rows(self) -> list[dict[str, str]]:
        if self.path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
            import pandas as pd

            try:
                frame = pd.read_excel(self.path, dtype=str, keep_default_na=False)
            except PermissionError:
                # Excel holds an exclusive lock on a workbook it has open, but
                # Windows still lets it be copied. Read the last saved version
                # from a copy rather than making someone close Excel first.
                import shutil
                import tempfile

                with tempfile.TemporaryDirectory() as scratch:
                    copy = Path(scratch) / self.path.name
                    shutil.copy2(self.path, copy)
                    frame = pd.read_excel(copy, dtype=str, keep_default_na=False)
            return [
                {str(k): ("" if v is None else str(v)) for k, v in row.items()}
                for row in frame.to_dict(orient="records")
            ]

        # utf-8-sig strips the BOM Excel writes when it saves a CSV.
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

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
        raw_rows = self._raw_rows()
        if not raw_rows:
            return
        mapping = self.resolve_columns(raw_rows[0].keys())

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
