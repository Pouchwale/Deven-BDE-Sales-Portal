"""The confirmed organisation and configuration.

The roster below is the list as confirmed by the business, transcribed once.
If somebody edits app/seeds/roster.py, this file says whether the edit was
intended.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.constants import Role
from app.models.org import Department, Team, User
from app.models.system import AppSetting

# name -> (role, manager, team)
CONFIRMED_ROSTER: dict[str, tuple[str, str | None, str | None]] = {
    "Portal Owner":        (Role.SUPER_ADMIN, None, None),
    "Shail Patel":         (Role.ADMIN, None, "Management"),

    "Navya Rupawat":       (Role.MANAGER, "Shail Patel", "BDE"),
    "Parth Fulvani":       (Role.BDE, "Navya Rupawat", "BDE"),
    "Muskan Makhija":      (Role.BDE, "Navya Rupawat", "BDE"),
    "Aastha Ramchandani":  (Role.BDE, "Navya Rupawat", "BDE"),
    "Shivani Patel":       (Role.BDE, "Navya Rupawat", "BDE"),

    "Ramanesh Nair":       (Role.MANAGER, "Shail Patel", "Sales"),
    "Shailesh Prajapati":  (Role.MANAGER, "Ramanesh Nair", "Sales"),
    "Parag Sharma":        (Role.BDE, "Shailesh Prajapati", "Sales"),
    "Sanjeev Singh":       (Role.BDE, "Shailesh Prajapati", "Sales"),
    "Nidhi Ratnakar":      (Role.BDE, "Shailesh Prajapati", "Sales"),
    "Pankaj":              (Role.BDE, "Shailesh Prajapati", "Sales"),
    "Urvish Dave":         (Role.BDE, "Shailesh Prajapati", "Sales"),
    "Lovjeet":             (Role.BDE, "Shailesh Prajapati", "Sales"),

    # Unassigned: no manager, no team, visible to ADMIN / SUPER_ADMIN only.
    "Kevin":               (Role.BDE, None, None),
    "Mohil":               (Role.BDE, None, None),
    "Lakhwinder Pal":      (Role.BDE, None, None),
    "Bimal":               (Role.BDE, None, None),
    "Diya Chawla":         (Role.BDE, None, None),
    "Apurva Shah":         (Role.BDE, None, None),
    "Bhakti Shah":         (Role.BDE, None, None),
}


def test_the_roster_is_exactly_the_confirmed_list(db) -> None:
    names = set(db.execute(select(User.name)).scalars().all())
    assert names == set(CONFIRMED_ROSTER)
    assert db.scalar(select(func.count(User.id))) == 22


@pytest.mark.parametrize("name,expected", CONFIRMED_ROSTER.items())
def test_each_person_has_the_confirmed_role_manager_and_team(
    db, users, name: str, expected: tuple[str, str | None, str | None]
) -> None:
    role, manager_name, team_name = expected
    user = users[name]

    assert user.role == role
    assert (user.manager.name if user.manager else None) == manager_name
    assert (user.team.name if user.team else None) == team_name
    assert user.is_active is True
    # Every seeded account starts on a shared password it must replace.
    #
    # Except the Super Admin, and only in the test harness: `conftest._database`
    # clears the flag for that one account after seeding, so tests can look a
    # customer up (the book is Super Admin only) without opening a second
    # database session and deadlocking against their own transaction. What the
    # seed itself writes is still proven, by the other twenty-one accounts.
    if user.role != Role.SUPER_ADMIN:
        assert user.must_change_password is True


def test_sales_team_members_hold_the_bde_role(db, users) -> None:
    """Confirmed: the tree annotates every leaf as (BDE), including the six in
    the Sales team. BDE and SALES share rank 3, so this is a reporting
    distinction rather than a permissions one - the SALES role exists and is
    simply unused by the seed."""
    for name in ("Parag Sharma", "Sanjeev Singh", "Nidhi Ratnakar",
                 "Pankaj", "Urvish Dave", "Lovjeet"):
        assert users[name].role == Role.BDE
        assert users[name].team.name == "Sales"

    assert db.scalar(select(func.count(User.id)).where(User.role == Role.SALES)) == 0


def test_there_are_no_department_heads(db) -> None:
    """Confirmed: do not add department heads.

    The attribute stays in the schema, unused, so appointing one later is a
    data change rather than a migration. Until one is appointed, only ADMIN
    and SUPER_ADMIN can read feedback - a plain MANAGER has no feedback scope.
    """
    heads = db.execute(
        select(User).where(User.heads_department_id.is_not(None))
    ).scalars().all()
    assert heads == []


def test_lookups_are_seeded(db) -> None:
    teams = set(db.execute(select(Team.name)).scalars().all())
    assert teams == {"Management", "BDE", "Sales"}

    departments = set(db.execute(select(Department.name)).scalars().all())
    assert departments == {
        "Sales", "Production", "Quality", "Dispatch", "Accounts", "Customer Support",
    }


# --------------------------------------------------- defaults vs the env
def test_an_empty_env_value_does_not_blank_the_default(db) -> None:
    """A setting whose .env variable is unset arrives as an empty string. The
    seed must fall back to the value in roster.py rather than storing ''."""
    from app.core.config import settings

    assert settings.GOOGLE_SYNC_URL == ""
    # Seeded from roster.DEFAULT_SETTINGS, not from the blank environment.
    assert db.get(AppSetting, "company.name").value == "Pouchwale"
