"""Text normalisation shared by everything that matches strings from a file.

Two normalisers, defined once. They were previously copy-pasted into the SAP
importer, the feedback column mapper and the post-sale matcher - and two
subtly different normalisers is how a name stops matching itself.

  normalise_header  a spreadsheet column caption -> lookup key. Also strips
                    the BOM Excel leaves on the very first cell.
  match_key         a person's or company's name -> comparison key.
"""
from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")


def normalise_header(header: str) -> str:
    """`  Invoice  No ` -> `invoice no`."""
    return _WHITESPACE.sub(" ", (header or "").replace("﻿", "").strip()).lower()


def match_key(value: str | None) -> str:
    """`  Apurva   Shah ` -> `apurva shah`."""
    return _WHITESPACE.sub(" ", (value or "").strip()).lower()
