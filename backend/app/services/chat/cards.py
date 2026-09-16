"""Turning a tool result into something you can act on.

Section 7.5 of the plan: **four shapes only** - lead, account, department
rating, counted list. Everything else stays prose, because a wall of cards is
worse than a sentence.

This is a SECOND projection, deliberately narrower than the one the model
sees. Two rules make it safe, and both are load-bearing:

  * **Ids over records** (§17). A card carries an id, a name and the two or
    three facts you need to decide whether to click. The full record is read
    on the real page, under the normal UI permissions. So a card never
    carries a mobile number or an email address - not even from the
    single-record tools, which do return them to the model. The phone number
    is on the lead page, one click away, where the audit trail is.

  * **Nothing here is persisted** (§9). Cards ride the SSE stream to the
    browser of the person who asked, and stop there. `chat_messages` keeps
    prose; `chat_tool_calls` keeps a name, arguments and a row count. Reload
    a past conversation and the cards are gone - which is correct, not a bug.

The rows themselves are already scope-bound: they are the output of a tool
that passed `registry.dispatch`, so a BDE's cards contain a BDE's leads. This
module reshapes; it does not fetch, and it cannot widen anything.
"""
from __future__ import annotations

from typing import Any

from app.core.constants import ReferenceStatus

#: A handful per tool call. The point of a card is that it stands out; twelve
#: of them is a table, and a bad one.
MAX_PER_TOOL = 4

#: Which tools produce which shape. A tool absent from here answers in prose,
#: which is the default and usually the right one.
LEAD_TOOLS = {"list_leads", "get_lead", "list_overdue_leads"}
ACCOUNT_TOOLS = {"list_customers", "get_customer", "list_reference_accounts"}
PENDING_TOOLS = {"list_pending_feedback_requests"}
FOLLOW_UP_TOOLS = {"list_reference_followups"}
DEPARTMENT_TOOLS = {"get_feedback_analysis"}


def _badge(label: str, tone: str = "neutral") -> dict[str, str]:
    return {"label": label, "tone": tone}


def _lead_card(row: dict) -> dict[str, Any]:
    status = row.get("status") or ""
    badges = [
        _badge(
            # PENDING -> Pending, FOLLOW_UP -> Follow up. The UI has its own
            # label map; duplicating it here would be a second thing to keep
            # in step for no gain.
            str(status).replace("_", " ").capitalize(),
            "success" if status == "CONVERTED" else "danger" if status == "LOST" else "info",
        )
    ]
    if row.get("priority") in ("HIGH", "URGENT"):
        badges.append(_badge(str(row["priority"]).title(), "warning"))

    meta: list[str] = []
    if row.get("owner"):
        meta.append(str(row["owner"]))
    if row.get("next_follow_up_date"):
        meta.append(f"Follow up {row['next_follow_up_date']}")

    return {
        "kind": "lead",
        "id": row["id"],
        "title": row.get("name") or "Unnamed lead",
        "subtitle": row.get("company"),
        "badges": badges,
        "meta": meta,
        "href": "/leads",
        "action": "Open lead",
    }


def _account_card(row: dict) -> dict[str, Any]:
    status = row.get("reference_status") or ReferenceStatus.NOT_ASKED
    tone = {
        ReferenceStatus.TAKEN: "success",
        ReferenceStatus.PENDING: "warning",
        ReferenceStatus.DECLINED: "danger",
    }.get(status, "neutral")

    badges = [_badge(str(status).replace("_", " ").title(), tone)]
    if row.get("sap_code"):
        badges.append(_badge(str(row["sap_code"]), "neutral"))
    elif row.get("type") == "LEAD":
        badges.append(_badge("Converted lead", "info"))

    meta: list[str] = []
    if row.get("owner"):
        meta.append(str(row["owner"]))
    if row.get("last_invoice_date"):
        meta.append(f"Last invoice {row['last_invoice_date']}")

    return {
        "kind": "account",
        "id": row["id"],
        "title": row.get("name") or "Unnamed account",
        "subtitle": row.get("company"),
        "badges": badges,
        "meta": meta,
        # A SAP customer has its own page; a converted lead does not.
        "href": f"/customers/{row['id']}" if row.get("type") != "LEAD" else "/references?tab=customers",
        "action": "Open account",
    }


def _department_card(row: dict) -> dict[str, Any]:
    average = row.get("average_rating")
    scale = row.get("scale_max") or 5
    below = bool(row.get("below_threshold"))

    return {
        "kind": "department",
        "id": row.get("department_id") or row.get("id") or row.get("department_name", ""),
        "title": row.get("department_name") or "Department",
        "subtitle": f"{row.get('response_count', 0)} ratings",
        "badges": [
            _badge(
                f"{average}/{scale}" if average is not None else "No ratings",
                "danger" if below else "success",
            ),
            *([_badge("Needs attention", "warning")] if below else []),
        ],
        "meta": [],
        "href": "/feedback?tab=analysis",
        "action": "Open feedback",
    }


def _pending_card(row: dict) -> dict[str, Any]:
    return {
        "kind": "account",
        "id": row["id"],
        "title": row.get("name") or "Unnamed",
        "subtitle": row.get("company_name"),
        "badges": [
            _badge("Converted lead" if row.get("type") == "LEAD" else "Customer", "info"),
            _badge("Owed feedback", "warning"),
        ],
        "meta": [str(row["owner_name"])] if row.get("owner_name") else [],
        "href": "/feedback?tab=pending",
        "action": "Open queue",
    }


def _follow_up_card(row: dict) -> dict[str, Any]:
    overdue = row.get("days_overdue") or 0
    return {
        "kind": "account",
        "id": row.get("subject_id") or row.get("id", ""),
        "title": row.get("subject_name") or row.get("name") or "Unnamed",
        "subtitle": None,
        "badges": [
            _badge(
                f"{overdue} days late" if overdue > 0 else "Due today",
                "danger" if overdue > 0 else "warning",
            )
        ],
        "meta": [str(row["owner_name"])] if row.get("owner_name") else [],
        "href": "/references?tab=follow-ups",
        "action": "Open follow-ups",
    }


def _counted(name: str, result: dict) -> list[dict[str, Any]]:
    """Shape four: a number, and a way to go and see it.

    For the stats tools, where the answer IS the count and listing four
    arbitrary rows out of forty would be misleading.
    """
    if name == "get_lead_stats":
        open_leads = result.get("open", 0)
        if not open_leads:
            return []
        return [
            {
                "kind": "count",
                "id": "lead-stats",
                "title": f"{open_leads} open {'lead' if open_leads == 1 else 'leads'}",
                "subtitle": f"{result.get('follow_ups_due', 0)} due · {result.get('no_recent_activity', 0)} gone quiet",
                "badges": [],
                "meta": [],
                "href": "/leads",
                "action": "Show me",
            }
        ]

    if name == "get_reference_stats":
        taken = result.get("references_taken", 0)
        total = result.get("converted_customers", 0) + result.get("converted_leads", 0)
        if not total:
            return []
        return [
            {
                "kind": "count",
                "id": "reference-stats",
                "title": f"{taken} of {total} accounts have given a reference",
                "subtitle": f"{result.get('not_asked', 0)} never asked",
                "badges": [],
                "meta": [],
                "href": "/references",
                "action": "Show me",
            }
        ]

    return []


def build(name: str, result: Any) -> list[dict[str, Any]]:
    """Cards for one tool result, or an empty list.

    Never raises: a card is a nicety, and a malformed row must not cost the
    user their answer. Anything unexpected simply produces no card and the
    prose carries the turn on its own.
    """
    try:
        if not isinstance(result, dict):
            return []

        if name in DEPARTMENT_TOOLS:
            rows = result.get("departments") or []
            # Only the ones that need somebody. A card per department is a
            # table, and the analysis page already is one.
            flagged = [row for row in rows if row.get("below_threshold")]
            return [_department_card(row) for row in flagged[:MAX_PER_TOOL]]

        if name in ("get_lead_stats", "get_reference_stats"):
            return _counted(name, result)

        items = result.get("items")
        if not isinstance(items, list):
            # A single-record tool returns the record itself.
            if name == "get_lead":
                return [_lead_card(result)]
            if name == "get_customer":
                return [_account_card(result)]
            return []

        rows = items[:MAX_PER_TOOL]
        if name in LEAD_TOOLS:
            return [_lead_card(row) for row in rows]
        if name in ACCOUNT_TOOLS:
            return [_account_card(row) for row in rows]
        if name in PENDING_TOOLS:
            return [_pending_card(row) for row in rows]
        if name in FOLLOW_UP_TOOLS:
            return [_follow_up_card(row) for row in rows]

        return []
    except Exception:  # noqa: BLE001 - a bad card must never cost an answer
        return []


#: Every key a card may ever carry. Asserted in the tests, so a field added
#: to a projection cannot reach the browser by simply being passed through.
CARD_KEYS = {"kind", "id", "title", "subtitle", "badges", "meta", "href", "action"}
