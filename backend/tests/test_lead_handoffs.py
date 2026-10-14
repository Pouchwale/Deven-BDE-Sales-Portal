"""Passing a lead on: the holder hands an open lead to somebody in their own
team, it leaves their list, and every pass is kept in a log with counts.
No score moves because of it."""
from __future__ import annotations

from app.services import metrics
from tests.conftest import advance_lead, eligible_lead, sign_in, super_admin_headers


def held_by(client, users, holder: str, *, name: str = "Pass On Co") -> dict:
    """A new lead assigned to `holder` by Shail, who can act on everyone."""
    response = client.post(
        "/api/leads",
        headers=sign_in(client, users["Shail Patel"]),
        json={"name": name, "assigned_to_user_id": str(users[holder].id)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def pass_on(client, headers, lead_id: str, to_user, reason: str | None = None):
    return client.post(
        f"/api/leads/{lead_id}/pass-on",
        headers=headers,
        json={"to_user_id": str(to_user.id), "reason": reason},
    )


# ----------------------------------------------------------- passing on
def test_navya_passes_her_lead_to_muskan(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    lead = held_by(client, users, "Navya Rupawat")

    response = pass_on(client, navya, lead["id"], users["Muskan Makhija"], "She knows them.")
    assert response.status_code == 200, response.text
    moved = response.json()
    assert moved["assigned_to_user_id"] == str(users["Muskan Makhija"].id)
    assert moved["assigned_by_name"] == "Navya Rupawat"
    passed = [a for a in moved["activities"] if a["activity_type"] == "PASSED_ON"]
    assert len(passed) == 1
    assert "Muskan Makhija" in passed[0]["remark"] and "She knows them." in passed[0]["remark"]

    muskan = sign_in(client, users["Muskan Makhija"])
    mine = client.get(
        "/api/leads", headers=muskan, params={"assigned_to": str(users["Muskan Makhija"].id)}
    ).json()
    assert lead["id"] in [item["id"] for item in mine["items"]]
    told = client.get("/api/notifications", headers=muskan).json()["items"]
    assert any(n["title"] == "Lead passed on to you: Pass On Co" for n in told)

    # Off Navya's own list.
    still_hers = client.get(
        "/api/leads", headers=navya, params={"assigned_to": str(users["Navya Rupawat"].id)}
    ).json()
    assert lead["id"] not in [item["id"] for item in still_hers["items"]]


def test_only_the_person_holding_the_lead_can_pass_it_on(client, users) -> None:
    lead = held_by(client, users, "Parth Fulvani")
    for who in ("Navya Rupawat", "Shail Patel"):
        response = pass_on(client, sign_in(client, users[who]), lead["id"], users["Muskan Makhija"])
        assert response.status_code == 403, (who, response.text)


def test_only_to_somebody_in_your_own_team(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    lead = held_by(client, users, "Navya Rupawat")
    # Parag is in Sales, under Shailesh - not Navya's team.
    assert pass_on(client, navya, lead["id"], users["Parag Sharma"]).status_code == 403
    assert pass_on(client, navya, lead["id"], users["Navya Rupawat"]).status_code == 422


def test_somebody_without_a_team_has_nobody_to_pass_to(client, users) -> None:
    lead = held_by(client, users, "Muskan Makhija")
    muskan = sign_in(client, users["Muskan Makhija"])
    assert pass_on(client, muskan, lead["id"], users["Parth Fulvani"]).status_code == 403


def test_a_closed_lead_cannot_be_passed_on(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    lead = held_by(client, users, "Navya Rupawat")
    advance_lead(client, navya, lead["id"])
    response = pass_on(client, navya, lead["id"], users["Muskan Makhija"])
    assert response.status_code == 422, response.text
    assert client.get("/api/leads/handoffs", headers=navya).json()["total"] == 0


def test_passing_on_moves_no_score(client, db, users) -> None:
    eligible_lead(client, users, assignee="Muskan Makhija", name="Scored Account")
    people = [users["Navya Rupawat"].id, users["Muskan Makhija"].id]
    before = metrics.reference_scores_by_user(db, people)
    assert before[users["Muskan Makhija"].id]["eligible"] == 1

    lead = held_by(client, users, "Navya Rupawat")
    navya = sign_in(client, users["Navya Rupawat"])
    assert pass_on(client, navya, lead["id"], users["Muskan Makhija"]).status_code == 200

    assert metrics.reference_scores_by_user(db, people) == before


# ------------------------------------------------------------------ the log
def test_the_log_keeps_every_pass_and_counts_them(client, users) -> None:
    navya = sign_in(client, users["Navya Rupawat"])
    first = held_by(client, users, "Navya Rupawat", name="First Co")
    second = held_by(client, users, "Navya Rupawat", name="Second Co")
    assert pass_on(client, navya, first["id"], users["Muskan Makhija"], "Hers now").status_code == 200
    assert pass_on(client, navya, second["id"], users["Parth Fulvani"]).status_code == 200

    log = client.get("/api/leads/handoffs", headers=navya).json()
    assert log["total"] == 2
    assert [(row["name"], row["count"]) for row in log["passed_by"]] == [("Navya Rupawat", 2)]
    assert sorted((row["name"], row["count"]) for row in log["passed_to"]) == [
        ("Muskan Makhija", 1),
        ("Parth Fulvani", 1),
    ]
    newest = log["items"][0]
    assert newest["lead_name"] == "Second Co"
    assert newest["from_name"] == "Navya Rupawat" and newest["to_name"] == "Parth Fulvani"
    assert log["items"][1]["reason"] == "Hers now"

    # Each person sees the passes that involve people they can see.
    muskan = client.get("/api/leads/handoffs", headers=sign_in(client, users["Muskan Makhija"])).json()
    assert [row["lead_name"] for row in muskan["items"]] == ["First Co"]
    parag = client.get("/api/leads/handoffs", headers=sign_in(client, users["Parag Sharma"])).json()
    assert parag["total"] == 0
    assert client.get("/api/leads/handoffs", headers=super_admin_headers(client)).json()["total"] == 2
