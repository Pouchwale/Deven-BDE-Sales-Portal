"""Shared field rules, defined once.

A validation rule written twice is a rule that will eventually disagree with
itself. Everything here is the single definition used by the schemas, the
services and the importers, so "what counts as a phone number" has exactly one
answer in this codebase.
"""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator

# India. Ten digits is the whole national number; anything shorter or longer is
# not a mobile, whatever else it might be.
NATIONAL_NUMBER_LENGTH = 10
COUNTRY_CODE = "91"

_NON_DIGITS = re.compile(r"\D+")


class InvalidPhone(ValueError):
    """Raised with wording safe to show a person."""


def normalise_phone(value: str | None) -> str | None:
    """Ten digits, or an error. Blank stays blank.

    Normalise FIRST, then judge - the order matters. The SAP book stores real
    numbers as `+91 97111 22505` and `091-9711122505`, which are the same
    number written three ways. Validating the raw string would reject two
    genuine customers on the next import; validating the digits accepts all
    three and stores one canonical form.

    What is deliberately NOT done here is rescuing a number that is simply
    wrong. Nine digits do not become ten, and letters do not become digits.
    The rule is "recognise the same number written differently", never "make a
    broken value look valid".
    """
    if value is None:
        return None

    text = value.strip()
    if not text:
        return None

    digits = _NON_DIGITS.sub("", text)
    if not digits:
        raise InvalidPhone("Enter a valid 10-digit mobile number.")

    # Peel the two prefixes a number can carry, longest form first:
    # `091-9711122505` is a trunk zero AND a country code on the same number,
    # which is how the SAP book writes some of them.
    #
    # Exactly one zero, and only when something remains underneath - so
    # "0000000000" stays ten digits of nonsense and is rejected below rather
    # than being whittled down to nothing.
    if len(digits) > NATIONAL_NUMBER_LENGTH and digits.startswith("0"):
        digits = digits[1:]
    # 91XXXXXXXXXX -> XXXXXXXXXX. Length-gated, so a genuine ten-digit number
    # that happens to begin 91 is left alone.
    if len(digits) == len(COUNTRY_CODE) + NATIONAL_NUMBER_LENGTH and digits.startswith(
        COUNTRY_CODE
    ):
        digits = digits[len(COUNTRY_CODE) :]

    if len(digits) != NATIONAL_NUMBER_LENGTH:
        raise InvalidPhone("Enter a valid 10-digit mobile number.")
    return digits


def _validate(value: str | None) -> str | None:
    """Pydantic entry point. Raises ValueError, which becomes a 422."""
    try:
        return normalise_phone(value)
    except InvalidPhone as error:
        raise ValueError(str(error)) from None


#: Use on every field that holds a phone number. Stores the canonical ten
#: digits, so two records of the same person compare equal.
Phone = Annotated[str | None, AfterValidator(_validate)]
