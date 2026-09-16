"""When a won account becomes work.

One rule, defined once, because it is asked in four places - the reference
queue, the feedback queue, and the denominator of a KPI on each. Four copies
of "invoice date plus ten days" is four chances to disagree about what the
company's reference rate actually is.

THE RULE
--------
An account becomes eligible for a reference ask and for a feedback request
**ten days after SAP invoiced it**, not before.

    eligible_on = first_invoice_date + 10 days
    eligible    = today >= eligible_on

The waiting period exists because asking somebody how the work went on the day
they were invoiced asks about nothing they have received yet.

THE SOURCE OF TRUTH
-------------------
`customers.first_invoice_date` - the SAP invoice date, not `created_at` and not
`imported_at`. When the row was typed into this database says nothing about
when the customer was served.

First invoice rather than latest, deliberately: the gate is "has this account
been a customer for ten days", which is answered once. Keying it to the latest
invoice would make a repeat customer ineligible again on every new invoice,
pulling accounts that had already been asked back out of the queue.

NO INVOICE, NO ELIGIBILITY
--------------------------
A record with no invoice date is not eligible, and that is the whole answer for
converted leads. A lead that converts is a won deal, but it is not yet a SAP
account: it goes to SAP, SAP invoices it, and it arrives here as a customer.
Only then does the clock start. Conversion alone does not open reference or
feedback work - see BUSINESS_RULES_CHANGE_AUDIT.md, decision Q3.

Dates, not timestamps: an invoice is dated, not timed, so "ten days later"
is a calendar question and `date.today()` in the configured timezone is the
right clock. There is no hour at which this flips for one user and not
another.
"""
from __future__ import annotations

from datetime import date, timedelta

#: Days between the SAP invoice and the account becoming actionable.
ELIGIBILITY_DAYS = 10


def eligible_on(invoice_date: date | None) -> date | None:
    """The first day this account may be asked, or None if it has no invoice."""
    if invoice_date is None:
        return None
    return invoice_date + timedelta(days=ELIGIBILITY_DAYS)


def ready_on(
    invoice_date: date | None, reference_date: date | None = None
) -> date | None:
    """The day this account may be asked - the SAP sheet's own Reference Date.

    The workbook carries that column, so it is the answer when it is there:
    the portal must not recompute a date the business already publishes, or
    the two disagree the day SAP changes the rule. `eligible_on` remains the
    fallback for anything that did not come from the workbook.
    """
    if reference_date is not None:
        return reference_date
    return eligible_on(invoice_date)


def is_eligible(invoice_date: date | None, *, today: date | None = None) -> bool:
    """Whether the waiting period has passed.

    Exactly ten days counts as eligible - `>=`, not `>`. "Ten days after the
    invoice" reads as "on the tenth day", and an off-by-one here is a customer
    nobody asks for an extra day.
    """
    start = eligible_on(invoice_date)
    if start is None:
        return False
    return (today or date.today()) >= start


def cutoff(*, today: date | None = None) -> date:
    """The latest invoice date that is already eligible.

    The SQL form of the same rule: `first_invoice_date <= cutoff()`. Used so
    the queues can filter in the database rather than loading every account and
    deciding in Python (§39).
    """
    return (today or date.today()) - timedelta(days=ELIGIBILITY_DAYS)
