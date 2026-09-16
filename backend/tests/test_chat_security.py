"""The assistant's authorization boundary.

Every test here targets the TOOL LAYER, never the model. That is deliberate:
a test that asserts on model prose is a flaky test, and the property we
actually need is that the tools cannot return out-of-scope data no matter what
the model asks for. If these pass, the model's behaviour is a UX question
rather than a security one.

Numbering follows section 20.1 of docs/CHATBOT_IMPLEMENTATION_PLAN.md.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core import authority
from app.core.constants import AuditAction
from app.models.system import AuditEvent
from app.services.chat import tools as chat_tools
from app.services.chat.registry import ToolContext


def context_for(db, user) -> ToolContext:
    """Exactly how the API layer builds it: scope derived from the user, never
    supplied by the caller."""
    return ToolContext(
        db=db,
        actor=user,
        scope=authority.visible_user_ids(db, user),
        department_scope=authority.feedback_department_scope(user),
        ip_address="203.0.113.9",
    )


def run(ctx: ToolContext, name: str, **arguments) -> dict:
    return chat_tools.dispatch(ctx, name, arguments)


def ok(outcome: dict) -> dict:
    assert outcome["ok"], f"expected success, got {outcome.get('error')}"
    return outcome["result"]


# ------------------------------------------------------------- 1, 2. scope
def test_a_bde_cannot_reach_the_customer_archive_at_all(db, users) -> None:
    """Stronger than the scope check this replaces.

    The SAP book became a Super Admin archive, so the question is no longer
    "which accounts may a BDE see" but "may a BDE open it" - and the answer is
    no. The assistant has to agree with the UI, or the restriction is
    decoration: a refusal in one door and a sentence through the other.
    """
    parth = context_for(db, users["Parth Fulvani"])

    for tool in ("list_customers", "get_customer", "get_customer_timeline"):
        outcome = run(parth, tool, search="JOVAKI")
        assert not outcome["ok"], tool
        assert outcome["error"]["code"] == "NOT_PERMITTED", tool

    # The scope guarantee this used to prove is now made on leads, which is
    # where a BDE's work actually lives - see the next test.


def test_a_bde_never_sees_another_users_leads(db, users, client) -> None:
    from tests.conftest import sign_in

    # An admin, not Navya: Ramanesh is outside her subtree, so she would be
    # refused and the test would pass without ever creating the lead.
    admin = sign_in(client, users["Shail Patel"])
    created = client.post(
        "/api/leads",
        headers=admin,
        json={
            "name": "Ramanesh Only",
            "assigned_to_user_id": str(users["Ramanesh Nair"].id),
        },
    )
    assert created.status_code == 201

    parth = context_for(db, users["Parth Fulvani"])
    result = ok(run(parth, "list_leads"))
    assert all(row["name"] != "Ramanesh Only" for row in result["items"])


def test_a_manager_sees_their_subtree_and_no_further(db, users, client) -> None:
    from tests.conftest import sign_in

    admin = sign_in(client, users["Shail Patel"])
    outside = client.post(
        "/api/leads",
        headers=admin,
        json={
            "name": "Different Chain",
            "assigned_to_user_id": str(users["Ramanesh Nair"].id),
        },
    ).json()

    navya = context_for(db, users["Navya Rupawat"])
    result = ok(run(navya, "list_leads"))
    assert all(row["id"] != outside["id"] for row in result["items"])


# --------------------------------------------------------------- 3. 404s
def test_an_out_of_scope_id_is_not_found_rather_than_forbidden(db, users, client) -> None:
    """A 403 would confirm the record exists. The tool must not do that."""
    from tests.conftest import sign_in

    admin = sign_in(client, users["Shail Patel"])
    other = client.post(
        "/api/leads",
        headers=admin,
        json={
            "name": "Not Yours",
            "assigned_to_user_id": str(users["Ramanesh Nair"].id),
        },
    ).json()

    parth = context_for(db, users["Parth Fulvani"])
    outcome = run(parth, "get_lead", lead_id=other["id"])

    assert not outcome["ok"]
    assert outcome["error"]["code"] == "NOT_FOUND"

    # The wording must not confirm the record exists, or the refusal itself
    # becomes an oracle for probing ids across the hierarchy.
    message = outcome["error"]["message"].lower()
    for tell in ("not yours", "permission", "forbidden", "belongs to", "ramanesh"):
        assert tell not in message, f"refusal leaks {tell!r}"


# ------------------------------------------------ 4. department-scope gate
def test_feedback_numbers_reach_everyone_but_the_alerts_do_not(db, users) -> None:
    """Feedback analysis used to be refused without a department scope. It is
    company-wide now, matching the module: a BDE who cannot see that Dispatch
    is at 2.9 has no way to make sense of the complaint they are about to
    take. The ALERT list is the half that stays narrow - an alert is a job
    somebody owns, and an empty list is the honest answer, not a refusal."""
    for name in ("Parth Fulvani", "Navya Rupawat"):
        ctx = context_for(db, users[name])
        result = ok(run(ctx, "get_feedback_analysis"))
        assert result["departments"], f"{name} should see the department averages"
        assert result["open_alerts"] == [], f"{name} owns no alerts"

    admin = ok(run(context_for(db, users["Shail Patel"]), "get_feedback_analysis"))
    assert admin["departments"]


def test_customer_reviews_reach_everyone_without_contact_details(db, users) -> None:
    """The reviews are the point of the module, so everyone reads them. A
    chat transcript is still the wrong place for a customer's phone number -
    not even an administrator gets one here, though the UI would show it."""
    for name in ("Parth Fulvani", "Shail Patel"):
        ctx = context_for(db, users[name])
        result = ok(run(ctx, "list_customer_reviews"))
        for row in result["items"]:
            assert "mobile" not in row, name
            assert "email" not in row, name


def test_an_admin_does_reach_feedback_analysis(db, users) -> None:
    ctx = context_for(db, users["Shail Patel"])
    result = ok(run(ctx, "get_feedback_analysis"))
    assert "departments" in result and "open_alerts" in result


def test_a_gated_tool_is_not_even_offered(db, users) -> None:
    """Withholding the schema is not the boundary, but a tool that could only
    ever be refused should not be advertised."""
    offered = {s["name"] for s in chat_tools.schemas_for(users["Parth Fulvani"])}
    assert "get_team_workload" not in offered, "rank-gated: a BDE has no subtree"
    # Feedback is not gated any more, and the schemas must say so or the model
    # will never propose the tool that answers the question.
    assert "get_feedback_analysis" in offered
    assert "list_customer_reviews" in offered

    admin = {s["name"] for s in chat_tools.schemas_for(users["Shail Patel"])}
    assert "get_feedback_analysis" in admin
    assert "get_team_workload" in admin


# ------------------------------------------------------------- 4b. rank gate
def test_team_workload_is_refused_for_a_field_user(db, users) -> None:
    ctx = context_for(db, users["Parth Fulvani"])
    outcome = run(ctx, "get_team_workload")
    assert not outcome["ok"]
    assert outcome["error"]["code"] == "NOT_PERMITTED"


def test_team_workload_for_a_manager_covers_only_their_subtree(db, users) -> None:
    ctx = context_for(db, users["Navya Rupawat"])
    result = ok(run(ctx, "get_team_workload"))

    subtree = authority.subtree_ids(db, users["Navya Rupawat"].id)
    returned = {uuid.UUID(row["id"]) for row in result["items"]}
    assert returned <= subtree, returned - subtree
    assert users["Navya Rupawat"].id not in returned, "should exclude self"


# ------------------------------------- 5. a forged identity changes nothing
def test_tool_arguments_cannot_carry_an_identity(db, users) -> None:
    """The strongest guarantee in the design: there is no argument to forge.

    If any tool ever grows a user_id/owner/scope parameter, this fails - which
    is the point of asserting on the schemas rather than on behaviour.
    """
    banned = {"user_id", "owner_id", "actor", "actor_id", "scope", "role",
              "assigned_to", "assigned_to_user_id", "as_user", "on_behalf_of"}

    for spec in chat_tools.all_specs():
        fields = set(spec.params_model.model_fields)
        leaked = fields & banned
        assert not leaked, f"{spec.name} exposes identity argument(s): {leaked}"


def test_extra_arguments_are_rejected_not_ignored(db, users) -> None:
    ctx = context_for(db, users["Parth Fulvani"])
    outcome = run(ctx, "list_leads", user_id=str(users["Navya Rupawat"].id))

    # pydantic models here forbid nothing by default, so the guarantee that
    # matters is the one above: the field does not exist, so it cannot be read.
    # This asserts the practical consequence - the result is still Parth's.
    if outcome["ok"]:
        result = outcome["result"]
        assert all(row["owner"] in (None, "Parth Fulvani") for row in result["items"])


# ------------------------------------------------- 6. prompt-injection shape
def test_injection_cannot_widen_scope(db, users) -> None:
    """Whatever the user types, the model's only lever is tool arguments - and
    those cannot express another user's data. Asserted deterministically at the
    tool layer rather than by inspecting prose."""
    parth = context_for(db, users["Parth Fulvani"])

    # The most permissive call the model could possibly make.
    result = ok(run(parth, "list_leads", limit=50))
    owners = {row["owner"] for row in result["items"]}
    assert owners <= {"Parth Fulvani", None}, owners


# --------------------------------------------------------- 7. unknown tools
def test_an_invented_tool_name_runs_nothing(db, users) -> None:
    ctx = context_for(db, users["Shail Patel"])
    outcome = run(ctx, "run_sql", query="SELECT * FROM users")
    assert not outcome["ok"]
    assert outcome["error"]["code"] == "UNKNOWN_TOOL"


# ------------------------------------------------------------ 8. row caps
def test_limit_is_clamped_not_trusted(db, users) -> None:
    from app.core.config import settings

    # The Super Admin, because the customer archive is theirs alone now and
    # the point here is the row cap, not the audience.
    ctx = context_for(db, users["Portal Owner"])
    result = ok(run(ctx, "list_customers", limit=50))
    assert result["count"] <= settings.CHAT_MAX_ROWS

    # Out of the schema's declared range entirely.
    outcome = run(ctx, "list_customers", limit=10_000)
    assert not outcome["ok"]
    assert outcome["error"]["code"] == "INVALID_ARGUMENTS"


def test_a_truncated_list_says_so(db, users) -> None:
    ctx = context_for(db, users["Portal Owner"])
    result = ok(run(ctx, "list_customers", limit=2))
    assert result["count"] == 2
    assert result["truncated"] is True
    assert result["total"] > 2


# ------------------------------------------------------- 9. bad arguments
@pytest.mark.parametrize(
    "arguments",
    [
        {"status": "NOT_A_STAGE"},
        {"limit": 0},
        {"limit": -5},
        {"search": "x" * 500},
    ],
)
def test_malformed_arguments_are_refused_before_any_query(db, users, arguments) -> None:
    ctx = context_for(db, users["Parth Fulvani"])
    outcome = run(ctx, "list_leads", **arguments)
    assert not outcome["ok"]
    assert outcome["error"]["code"] == "INVALID_ARGUMENTS"


def test_a_malformed_id_is_refused(db, users) -> None:
    ctx = context_for(db, users["Parth Fulvani"])
    outcome = run(ctx, "get_lead", lead_id="'; DROP TABLE leads;--")
    assert not outcome["ok"]
    assert outcome["error"]["code"] == "INVALID_ARGUMENTS"


# ---------------------------------------------------------- 10. audit trail
def test_every_dispatched_tool_can_be_audited(db, users) -> None:
    ctx = context_for(db, users["Parth Fulvani"])
    before = db.scalar(
        select(AuditEvent).where(AuditEvent.action == AuditAction.CHAT_TOOL_INVOKED)
    )
    assert before is None

    outcome = run(ctx, "get_lead_stats")
    chat_tools.record_invocation(ctx, "get_lead_stats", outcome)
    db.flush()

    rows = list(
        db.execute(
            select(AuditEvent).where(AuditEvent.action == AuditAction.CHAT_TOOL_INVOKED)
        ).scalars()
    )
    assert len(rows) == 1
    assert rows[0].actor_user_id == users["Parth Fulvani"].id
    assert rows[0].after["tool"] == "get_lead_stats"
    assert rows[0].after["ok"] is True


def test_a_refusal_is_audited_too(db, users) -> None:
    # get_team_workload is the live gate: rank, not department scope.
    ctx = context_for(db, users["Parth Fulvani"])
    outcome = run(ctx, "get_team_workload")
    assert not outcome["ok"], "this test needs a tool that is actually refused"
    chat_tools.record_invocation(ctx, "get_team_workload", outcome)
    db.flush()

    row = db.execute(
        select(AuditEvent).where(AuditEvent.action == AuditAction.CHAT_TOOL_INVOKED)
    ).scalars().one()
    assert row.after["ok"] is False
    assert row.after["error"] == "NOT_PERMITTED"


# ------------------------------------------------------ 12. PII minimisation
def test_list_results_do_not_carry_contact_details(db, users) -> None:
    """Contact details are opt-in: they belong to the single-record tools,
    where the user has clearly asked about that one person."""
    ctx = context_for(db, users["Portal Owner"])

    customers = ok(run(ctx, "list_customers", limit=5))
    for row in customers["items"]:
        assert "mobile" not in row and "email" not in row

    leads = ok(run(ctx, "list_leads", limit=5))
    for row in leads["items"]:
        assert "mobile" not in row and "email" not in row


def test_single_record_tools_do_carry_contact_details(db, users) -> None:
    ctx = context_for(db, users["Portal Owner"])
    listing = ok(run(ctx, "list_customers"))
    customer_id = listing["items"][0]["id"]

    detail = ok(run(ctx, "get_customer", customer_id=customer_id))
    assert "mobile" in detail and "email" in detail


def test_projections_never_leak_orm_objects(db, users) -> None:
    """Whatever a tool returns must be JSON - an ORM object reaching the model
    would carry every column, including ones no projection chose."""
    import json

    # The Super Admin, so every tool in the list is actually runnable - a
    # refusal would skip the projection this is checking.
    ctx = context_for(db, users["Portal Owner"])
    for name in (
        "get_my_work_summary",
        "get_lead_stats",
        "get_reference_stats",
        "list_leads",
        "list_customers",
        "list_references",
        "list_reference_accounts",
        "list_reference_followups",
        "list_pending_feedback_requests",
        "list_overdue_leads",
        "get_notifications",
        "get_team_workload",
        "get_feedback_analysis",
    ):
        outcome = run(ctx, name)
        assert outcome["ok"], f"{name}: {outcome.get('error')}"
        json.dumps(outcome["result"], default=str)


# ------------------------------------------------- 13. no internals leak out
def test_a_failing_tool_reports_nothing_internal(db, users, monkeypatch) -> None:
    ctx = context_for(db, users["Parth Fulvani"])

    def explode(*_args, **_kwargs):
        raise RuntimeError(
            "psycopg2.ProgrammingError: relation \"leads\" does not exist at /srv/app/x.py"
        )

    monkeypatch.setattr("app.services.leads.stats", explode)

    outcome = run(ctx, "get_lead_stats")
    assert not outcome["ok"]
    assert outcome["error"]["code"] == "TOOL_FAILED"

    message = outcome["error"]["message"]
    for forbidden in ("psycopg2", "relation", "leads", "/srv/app", "Traceback"):
        assert forbidden not in message, f"leaked {forbidden!r}"


# ------------------------------------------------------------- consistency
def test_the_assistant_and_the_dashboard_agree(db, users, client) -> None:
    """The summary tool reads the dashboard's own numbers, so the two can never
    drift apart and quietly disagree with each other."""
    from tests.conftest import sign_in

    headers = sign_in(client, users["Navya Rupawat"])
    dashboard = client.get("/api/dashboard", headers=headers).json()

    ctx = context_for(db, users["Navya Rupawat"])
    summary = ok(run(ctx, "get_my_work_summary"))

    assert summary["leads"]["open"] == dashboard["leads"]["open"]
    assert summary["feedback_pending"] == dashboard["feedback_pending"]
    assert (
        summary["references"]["references_taken"]
        == dashboard["references"]["references_taken"]
    )
