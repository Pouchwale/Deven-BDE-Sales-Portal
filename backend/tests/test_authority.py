"""The authority rules, asserted across the whole real org.

If a refactor changes any of these numbers, this file fails before anything
else does. That is the point: every other module trusts these answers.

  Rule 1  You may never act on somebody who outranks you.
  Rule 2  You may never hand out a role above your own.
  Rule 3  Demotion is not an escape hatch.
"""
from __future__ import annotations

import pytest

from app.core import authority
from app.core.constants import ALL_ROLES, ROLE_RANK, Role

# The expected scope sizes from plan v3 s4.2. These are assertions, not
# documentation: if the seed or the model changes, this table is the alarm.
EXPECTED_SCOPE_SIZES = {
    "Portal Owner": 22,
    "Shail Patel": 22,
    "Navya Rupawat": 5,          # self + Parth, Muskan, Aastha, Shivani
    "Ramanesh Nair": 8,          # self + Shailesh + his six
    "Shailesh Prajapati": 7,     # self + his six
    "Parth Fulvani": 1,
    "Parag Sharma": 1,
    "Apurva Shah": 1,
    "Kevin": 1,
}

UNASSIGNED = (
    "Kevin", "Mohil", "Lakhwinder Pal", "Bimal", "Diya Chawla",
    "Apurva Shah", "Bhakti Shah",
)


# ------------------------------------------------------------------ ranks
def test_rank_ordering() -> None:
    """The s2.2 map. Lower is more senior; BDE and SALES are equal."""
    assert ROLE_RANK[Role.SUPER_ADMIN] == 0
    assert ROLE_RANK[Role.ADMIN] == 1
    assert ROLE_RANK[Role.MANAGER] == 2
    assert ROLE_RANK[Role.BDE] == ROLE_RANK[Role.SALES] == 3
    assert (
        ROLE_RANK[Role.SUPER_ADMIN]
        < ROLE_RANK[Role.ADMIN]
        < ROLE_RANK[Role.MANAGER]
        < ROLE_RANK[Role.BDE]
    )


def test_seed_produces_the_documented_tree(users) -> None:
    assert len(users) == 22
    assert users["Shail Patel"].manager_id is None
    assert users["Navya Rupawat"].manager_id == users["Shail Patel"].id
    assert users["Ramanesh Nair"].manager_id == users["Shail Patel"].id
    # Three levels deep: the model must not assume two.
    assert users["Shailesh Prajapati"].manager_id == users["Ramanesh Nair"].id
    assert users["Parag Sharma"].manager_id == users["Shailesh Prajapati"].id
    for name in UNASSIGNED:
        assert users[name].manager_id is None, f"{name} should report to nobody"
        assert users[name].team_id is None, f"{name} should have no team"


# ------------------------------------------------------------- visibility
@pytest.mark.parametrize("name,expected", EXPECTED_SCOPE_SIZES.items())
def test_visibility_scope_sizes(db, users, name: str, expected: int) -> None:
    scope = authority.visible_user_ids(db, users[name])
    actual = 22 if scope is authority.ALL else len(scope)
    assert actual == expected, f"{name} sees {actual} users, expected {expected}"


def test_manager_sees_full_subtree_any_depth(db, users) -> None:
    """Ramanesh sees Parag, who is two levels below him."""
    scope = authority.visible_user_ids(db, users["Ramanesh Nair"])
    assert users["Shailesh Prajapati"].id in scope
    assert users["Parag Sharma"].id in scope
    assert users["Lovjeet"].id in scope


def test_manager_cannot_see_sideways(db, users) -> None:
    """Navya and Ramanesh are peers under Shail. Neither sees the other."""
    navya_scope = authority.visible_user_ids(db, users["Navya Rupawat"])
    assert users["Ramanesh Nair"].id not in navya_scope
    assert users["Parag Sharma"].id not in navya_scope

    ramanesh_scope = authority.visible_user_ids(db, users["Ramanesh Nair"])
    assert users["Navya Rupawat"].id not in ramanesh_scope
    assert users["Parth Fulvani"].id not in ramanesh_scope


def test_manager_cannot_see_above(db, users) -> None:
    scope = authority.visible_user_ids(db, users["Navya Rupawat"])
    assert users["Shail Patel"].id not in scope
    assert users["Portal Owner"].id not in scope


def test_field_user_sees_only_themselves(db, users) -> None:
    scope = authority.visible_user_ids(db, users["Parth Fulvani"])
    assert scope == {users["Parth Fulvani"].id}


def test_unassigned_users_visible_to_admin_only(db, users) -> None:
    """The seven unplaced accounts are Director-only by design."""
    for name in UNASSIGNED:
        target = users[name]
        assert authority.can_view_user(db, users["Shail Patel"], target.id)
        assert authority.can_view_user(db, users["Portal Owner"], target.id)
        assert not authority.can_view_user(db, users["Navya Rupawat"], target.id)
        assert not authority.can_view_user(db, users["Ramanesh Nair"], target.id)


# ----------------------------------------------------------------- Rule 1
@pytest.mark.parametrize(
    "actor,target,expected,why",
    [
        ("Shail Patel", "Navya Rupawat", True, "admin outranks, sees everyone"),
        ("Shail Patel", "Parag Sharma", True, "admin outranks"),
        ("Shail Patel", "Apurva Shah", True, "admin sees the unassigned"),
        ("Shail Patel", "Portal Owner", False, "target outranks"),
        ("Portal Owner", "Shail Patel", True, "super admin outranks the admin"),
        ("Shail Patel", "Shail Patel", False, "self-service is a different path"),
        ("Ramanesh Nair", "Shailesh Prajapati", True, "equal rank but an ancestor"),
        ("Shailesh Prajapati", "Ramanesh Nair", False, "equal rank, not an ancestor"),
        ("Navya Rupawat", "Parth Fulvani", True, "outranks, in subtree"),
        ("Navya Rupawat", "Parag Sharma", False, "not in her subtree"),
        ("Navya Rupawat", "Ramanesh Nair", False, "peer manager, not visible"),
        ("Navya Rupawat", "Shail Patel", False, "target outranks"),
        ("Parth Fulvani", "Muskan Makhija", False, "rank 3 outranks nobody"),
        ("Parth Fulvani", "Navya Rupawat", False, "target outranks"),
        ("Parag Sharma", "Parth Fulvani", False, "different chain, equal rank"),
        ("Apurva Shah", "Kevin", False, "unassigned users have no authority"),
    ],
)
def test_can_act_on_worked_examples(db, users, actor, target, expected, why) -> None:
    assert authority.can_act_on(db, users[actor], users[target]) is expected, why


def test_nobody_can_act_on_the_super_admin(db, users) -> None:
    owner = users["Portal Owner"]
    for name, user in users.items():
        if name == "Portal Owner":
            continue
        assert not authority.can_act_on(db, user, owner), f"{name} acted on the owner"


def test_no_user_can_act_on_themselves(db, users) -> None:
    """Self-service goes through PATCH /api/me, which cannot change a role."""
    for user in users.values():
        assert not authority.can_act_on(db, user, user)


def test_field_users_can_act_on_nobody(db, users) -> None:
    for actor in users.values():
        if actor.rank < 3:
            continue
        for target in users.values():
            assert not authority.can_act_on(db, actor, target)


def test_full_cross_product_is_consistent(db, users) -> None:
    """Every one of the 22x22 pairs, checked against the rules from scratch.

    A single asymmetry - two people who can each act on the other - would let
    a demotion war break the tree, so it is worth the 484 assertions.
    """
    people = list(users.values())
    for actor in people:
        for target in people:
            allowed = authority.can_act_on(db, actor, target)
            if not allowed:
                continue
            assert actor.id != target.id
            assert authority.can_view_user(db, actor, target.id)
            assert actor.rank <= target.rank
            if actor.rank == target.rank:
                assert authority.is_ancestor(db, actor, target)
            # Authority is never mutual.
            assert not authority.can_act_on(db, target, actor)


def test_actionable_matches_can_act_on(db, users) -> None:
    """The dropdown and the validation are derived from the same rule."""
    for name in ("Shail Patel", "Navya Rupawat", "Ramanesh Nair", "Parth Fulvani"):
        actor = users[name]
        expected = {u.id for u in users.values() if authority.can_act_on(db, actor, u)}
        assert authority.actionable_user_ids(db, actor) == expected


def test_actionable_set_sizes(db, users) -> None:
    """Navya's dropdown has four names, Shailesh's six, Shail's twenty."""
    assert len(authority.actionable_user_ids(db, users["Navya Rupawat"])) == 4
    assert len(authority.actionable_user_ids(db, users["Shailesh Prajapati"])) == 6
    # Everyone except themselves and the Super Admin.
    assert len(authority.actionable_user_ids(db, users["Shail Patel"])) == 20
    assert len(authority.actionable_user_ids(db, users["Portal Owner"])) == 21
    assert authority.actionable_user_ids(db, users["Parth Fulvani"]) == set()


# ----------------------------------------------------------------- Rule 2
@pytest.mark.parametrize(
    "name,expected",
    [
        ("Portal Owner", {"ADMIN", "MANAGER", "BDE", "SALES"}),
        ("Shail Patel", {"MANAGER", "BDE", "SALES"}),
        ("Navya Rupawat", {"BDE", "SALES"}),
        ("Parth Fulvani", set()),
        ("Apurva Shah", set()),
    ],
)
def test_assignable_roles(users, name, expected) -> None:
    assert set(authority.assignable_roles(users[name])) == expected


def test_cannot_grant_role_at_or_above_own(users) -> None:
    for user in users.values():
        for role in ALL_ROLES:
            granted = authority.can_grant_role(user, role)
            assert granted == (ROLE_RANK[role] > user.rank), (
                f"{user.name} ({user.role}) grant {role} -> {granted}"
            )


def test_an_admin_cannot_create_another_admin(users) -> None:
    """Q2: only a SUPER_ADMIN appoints administrators."""
    assert not authority.can_grant_role(users["Shail Patel"], Role.ADMIN)
    assert not authority.can_grant_role(users["Shail Patel"], Role.SUPER_ADMIN)
    assert authority.can_grant_role(users["Portal Owner"], Role.ADMIN)


def test_super_admin_cannot_grant_super_admin(users) -> None:
    """One owner account. A second would be a second lockout risk, and the
    rule is uniform: never grant a role at or above your own."""
    assert not authority.can_grant_role(users["Portal Owner"], Role.SUPER_ADMIN)


# --------------------------------------------------- reporting-line integrity
def test_cannot_set_self_as_manager(db, users) -> None:
    navya = users["Navya Rupawat"]
    assert authority.would_create_cycle(db, navya.id, navya.id)


def test_cycle_rejected(db, users) -> None:
    """Pointing Shail at Parag would close a loop through the whole chain."""
    assert authority.would_create_cycle(
        db, users["Shail Patel"].id, users["Parag Sharma"].id
    )
    assert authority.would_create_cycle(
        db, users["Ramanesh Nair"].id, users["Parag Sharma"].id
    )
    # A legitimate move is not a cycle.
    assert not authority.would_create_cycle(
        db, users["Parth Fulvani"].id, users["Shailesh Prajapati"].id
    )


def test_manager_rank_rules(users) -> None:
    navya = users["Navya Rupawat"]
    shail = users["Shail Patel"]
    parth = users["Parth Fulvani"]

    assert authority.manager_rank_is_valid(navya, Role.BDE)
    assert authority.manager_rank_is_valid(shail, Role.MANAGER)
    # The real Ramanesh -> Shailesh case: a MANAGER may manage a MANAGER.
    assert authority.manager_rank_is_valid(navya, Role.MANAGER)
    # A BDE may manage nobody, and nobody may manage an ADMIN but a super admin.
    assert not authority.manager_rank_is_valid(parth, Role.BDE)
    assert not authority.manager_rank_is_valid(navya, Role.ADMIN)


def test_ancestors_and_chain(db, users) -> None:
    parag_ancestors = authority.ancestor_ids(db, users["Parag Sharma"].id)
    assert parag_ancestors == {
        users["Shailesh Prajapati"].id,
        users["Ramanesh Nair"].id,
        users["Shail Patel"].id,
    }
    chain = authority.chain_to_top(db, users["Parag Sharma"])
    assert [u.name for u in chain] == [
        "Shailesh Prajapati",
        "Ramanesh Nair",
        "Shail Patel",
    ]


# ---------------------------------------------------------- feedback scope
def test_manager_without_flag_sees_no_feedback(users) -> None:
    """Reporting lines do not grant feedback: it is about rated departments."""
    assert authority.feedback_department_scope(users["Ramanesh Nair"]) == set()
    assert authority.feedback_department_scope(users["Navya Rupawat"]) == set()


def test_admins_see_all_feedback(users) -> None:
    assert authority.feedback_department_scope(users["Shail Patel"]) is authority.ALL
    assert authority.feedback_department_scope(users["Portal Owner"]) is authority.ALL


def test_department_head_flag_scopes_feedback_only(db, users) -> None:
    """A BDE who heads Quality is still a BDE everywhere else."""
    from app.models.org import Department

    quality = db.query(Department).filter_by(code="QUALITY").one()
    parth = users["Parth Fulvani"]
    parth.heads_department_id = quality.id
    db.flush()

    assert authority.feedback_department_scope(parth) == {quality.id}
    # The flag grants no authority over people and no extra visibility.
    assert authority.visible_user_ids(db, parth) == {parth.id}
    assert authority.actionable_user_ids(db, parth) == set()
    for target in users.values():
        assert not authority.can_act_on(db, parth, target)
