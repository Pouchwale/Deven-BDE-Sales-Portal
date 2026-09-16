"""Imports every tool module, which is what registers them.

Registration is an import side effect, so exactly one module may own the
imports - otherwise a tool exists or does not depending on what happened to be
imported first, and that is not a property you want in an authorization path.

Import this module; never the tools_* modules directly.
"""
from __future__ import annotations

from app.services.chat import (  # noqa: F401  (imported for side effects)
    tools_customers,
    tools_feedback,
    tools_general,
    tools_leads,
    tools_references,
)
from app.services.chat.registry import (
    ToolContext,
    ToolError,
    all_specs,
    dispatch,
    get,
    record_invocation,
    schemas_for,
)

__all__ = [
    "ToolContext",
    "ToolError",
    "all_specs",
    "dispatch",
    "get",
    "record_invocation",
    "schemas_for",
]
