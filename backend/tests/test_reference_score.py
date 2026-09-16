"""The reference score agreed with the business.

    score = -(eligible accounts with no reference / eligible accounts) x 100

Ten accounts with seven references reads -30%.
"""
from __future__ import annotations

import pytest

from app.services.metrics import reference_score
from tests.conftest import super_admin_headers


@pytest.mark.parametrize(
    "total,taken,expected",
    [
        (10, 7, -30.0),      # the worked example from the meeting
        (10, 10, 0.0),       # everything asked and answered
        (10, 0, -100.0),     # nobody asked
        (3, 1, -66.7),       # rounded to one decimal, never invented precision
        (0, 0, 0.0),         # nothing eligible: no gap to report, not -100%
    ],
)
def test_the_score_is_the_gap_left_to_close(total, taken, expected) -> None:
    assert reference_score(total, taken) == expected


def test_a_perfect_score_is_zero_not_minus_zero() -> None:
    """`-0.0` renders as "-0%", which reads as a penalty for asking everybody."""
    import math

    assert not math.copysign(1, reference_score(10, 10)) < 0


def test_the_score_matches_the_module_kpis(client) -> None:
    """The tile, the dashboard and the per-person table read one number."""
    headers = super_admin_headers(client)
    stats = client.get("/api/references/stats", headers=headers).json()
    eligible, taken = stats["eligible_accounts"], stats["references_taken"]
    assert stats["reference_score"] == reference_score(eligible, taken)

    dashboard = client.get("/api/dashboard", headers=headers).json()
    assert dashboard["references"]["reference_score"] == stats["reference_score"]

    for row in dashboard["reports"]:
        assert row["reference_score"] == reference_score(
            row["eligible_accounts"], row["references_on_eligible"]
        )
