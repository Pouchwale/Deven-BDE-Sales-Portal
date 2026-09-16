"""The organisation, as data.

The 22 accounts and the reporting tree of plan v3 s13, plus the lookup rows
the portal needs to function. Nothing here is transactional data - no leads,
no references and no feedback are invented. Customers come from the real SAP
export (data/sap/), and feedback will come from the real Google Forms export.

ASSUMPTION, flagged for confirmation: email addresses are not in the source
document. They are generated as firstname.lastname@pouchwale.com from the
names given. Change EMAIL_DOMAIN or the explicit `email` key below once the
real addresses are known - the seed matches on email, so correcting one is a
one-line edit plus a re-run.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.constants import Role, TeamCode
from app.seeds.message_templates import (
    FEEDBACK_EMAIL_BODY,
    FEEDBACK_EMAIL_SUBJECT,
    FEEDBACK_WHATSAPP,
)

EMAIL_DOMAIN = "pouchwale.com"


@dataclass(frozen=True, slots=True)
class SeedUser:
    name: str
    role: str
    title: str
    # The manager's NAME. Resolved to an id in a second pass, so the roster
    # can be written in any order and reads like the org chart.
    manager: str | None = None
    team_code: str | None = None
    email: str | None = None

    @property
    def resolved_email(self) -> str:
        if self.email:
            return self.email.lower()
        slug = re.sub(r"[^a-z0-9]+", ".", self.name.lower()).strip(".")
        return f"{slug}@{EMAIL_DOMAIN}"


TEAMS: tuple[tuple[str, str, int], ...] = (
    # (name, code, sort_order)
    ("Management", TeamCode.MANAGEMENT, 10),
    ("BDE", TeamCode.BDE, 20),
    ("Sales", TeamCode.SALES, 30),
)

# The rating dimensions. Provisional until the real Google Forms export
# arrives: s8.3 discovers departments from the form itself and reports any
# name it cannot match rather than inventing one.
DEPARTMENTS: tuple[tuple[str, str, int], ...] = (
    ("Sales", "SALES", 10),
    ("Production", "PROD", 20),
    ("Quality", "QUALITY", 30),
    ("Dispatch", "DISPATCH", 40),
    ("Accounts", "ACCOUNTS", 50),
    ("Customer Support", "SUPPORT", 60),
)


ROSTER: tuple[SeedUser, ...] = (
    # ---------------------------------------------------------- management
    SeedUser("Portal Owner", Role.SUPER_ADMIN, "Portal Owner", email=f"owner@{EMAIL_DOMAIN}"),
    SeedUser("Shail Patel", Role.ADMIN, "Director", team_code=TeamCode.MANAGEMENT),

    # ------------------------------------------------------------ BDE team
    SeedUser("Navya Rupawat", Role.MANAGER, "BDE Head", "Shail Patel", TeamCode.BDE),
    SeedUser("Parth Fulvani", Role.BDE, "BDE", "Navya Rupawat", TeamCode.BDE),
    SeedUser("Muskan Makhija", Role.BDE, "BDE", "Navya Rupawat", TeamCode.BDE),
    SeedUser("Aastha Ramchandani", Role.BDE, "BDE", "Navya Rupawat", TeamCode.BDE),
    SeedUser("Shivani Patel", Role.BDE, "BDE", "Navya Rupawat", TeamCode.BDE),

    # ---------------------------------------------------------- Sales team
    # Three levels deep on purpose: Shail -> Ramanesh -> Shailesh -> Parag.
    # The visibility model must not assume two.
    SeedUser("Ramanesh Nair", Role.MANAGER, "Sales Head", "Shail Patel", TeamCode.SALES),
    SeedUser("Shailesh Prajapati", Role.MANAGER, "Sales Manager", "Ramanesh Nair", TeamCode.SALES),
    SeedUser("Parag Sharma", Role.BDE, "Sales", "Shailesh Prajapati", TeamCode.SALES),
    SeedUser("Sanjeev Singh", Role.BDE, "Sales", "Shailesh Prajapati", TeamCode.SALES),
    SeedUser("Nidhi Ratnakar", Role.BDE, "Sales", "Shailesh Prajapati", TeamCode.SALES),
    SeedUser("Pankaj", Role.BDE, "Sales", "Shailesh Prajapati", TeamCode.SALES),
    SeedUser("Urvish Dave", Role.BDE, "Sales", "Shailesh Prajapati", TeamCode.SALES),
    SeedUser("Lovjeet", Role.BDE, "Sales", "Shailesh Prajapati", TeamCode.SALES),

    # ---------------------------------------------------------- unassigned
    # No team and no manager, so only ADMIN and SUPER_ADMIN can see them.
    # Intended behaviour, not a data gap (plan v3 s2.3, Q7). Apurva Shah and
    # Bhakti Shah own SAP accounts despite reporting to nobody.
    SeedUser("Kevin", Role.BDE, "BDE"),
    SeedUser("Mohil", Role.BDE, "BDE"),
    SeedUser("Lakhwinder Pal", Role.BDE, "BDE"),
    SeedUser("Bimal", Role.BDE, "BDE"),
    SeedUser("Diya Chawla", Role.BDE, "BDE"),
    SeedUser("Apurva Shah", Role.BDE, "BDE"),
    SeedUser("Bhakti Shah", Role.BDE, "BDE"),
)


# Runtime configuration seeded with the values from .env, editable later in
# the admin panel without a deploy.
DEFAULT_SETTINGS: tuple[tuple[str, str, str, str], ...] = (
    # (key, value_type, default, description)
    ("feedback.rating_scale_max", "int", "5", "Top of the feedback rating scale."),
    ("feedback.alert_threshold", "float", "3.0", "Average below which a department is flagged."),
    ("feedback.alert_min_responses", "int", "5", "Minimum responses before an alert can fire."),
    ("feedback.alert_window_days", "int", "30", "Rolling window for the department average."),
    ("reference.default_followup_days", "int", "30", "Default gap before asking for a reference again."),
    ("company.name", "str", "Pouchwale", "Used in outgoing message templates."),
    ("company.feedback_form_url", "str", "",
     "The Google Form customers fill in. Blank until the real form is supplied."),
    ("feedback.form_reference_entry_id", "str", "",
     "The form's prefill field id for the reference code, e.g. entry.4839201. "
     "From Google Forms > Get pre-filled link. Blank means requests are sent "
     "without a code and responses fall back to contact matching."),
    ("feedback.request_expires_days", "int", "30",
     "After this, an unanswered request stops counting as waiting."),
    ("message.feedback_whatsapp", "str", FEEDBACK_WHATSAPP,
     "WhatsApp text when asking a customer for feedback."),
    ("message.feedback_email_subject", "str", FEEDBACK_EMAIL_SUBJECT,
     "Email subject when asking for feedback."),
    ("message.feedback_email_body", "str", FEEDBACK_EMAIL_BODY,
     "Email body when asking for feedback."),
)
