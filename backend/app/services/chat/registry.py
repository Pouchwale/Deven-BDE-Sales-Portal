"""The tool registry and the gates every call passes through.

Dispatch order, and why each step exists:

  1. Name is in the registry        - an invented tool name runs nothing.
  2. Arguments validate             - pydantic, before any query is built.
  3. Caller outranks the gate       - rank check, from the JWT's user.
  4. Department scope, if required  - feedback analysis is not reporting-line
                                      scoped, so it needs its own gate.
  5. The tool runs with ToolContext - scope comes from here, never from args.

A failure at any step returns a tool_result with `is_error`, never an
exception: the model needs to be able to explain the refusal, and a raised
exception mid-loop would desynchronise the conversation.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core import authority
from app.core.authority import ALL, _All
from app.core.constants import ROLE_RANK, AuditAction, EntityType, Role
from app.core.config import settings
from app.models.org import User
from app.services import audit


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything a tool is allowed to know about who is asking.

    Built once per HTTP request from the authenticated session. A tool reads
    its scope from here; nothing the model sends can alter it.
    """

    db: Session
    actor: User
    scope: set[uuid.UUID] | _All
    department_scope: set[uuid.UUID] | _All
    ip_address: str | None = None

    @property
    def max_rows(self) -> int:
        return settings.CHAT_MAX_ROWS

    def clamp(self, requested: int | None) -> int:
        """The model may ask for fewer rows. It may not ask for more."""
        if requested is None or requested < 1:
            return self.max_rows
        return min(requested, self.max_rows)

    @property
    def has_department_scope(self) -> bool:
        return self.department_scope is ALL or bool(self.department_scope)


class ToolError(Exception):
    """A refusal the model should explain, not a crash."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    params_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], dict]
    # Lower rank is more senior (SUPER_ADMIN 0 ... BDE/SALES 3). A tool with
    # min_rank=MANAGER is refused for anyone less senior than a manager.
    min_rank: int = ROLE_RANK[Role.SALES]
    requires_department_scope: bool = False


_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in _REGISTRY:
        raise RuntimeError(f"Duplicate chat tool {spec.name!r}")
    _REGISTRY[spec.name] = spec
    return spec


def get(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def all_specs() -> list[ToolSpec]:
    """Deterministic order. The tool list is part of the cached prompt prefix,
    so a set-iteration order would silently destroy the cache hit rate."""
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]


def schemas_for(actor: User) -> list[dict]:
    """The tool definitions to send, filtered to what this caller may run.

    Withholding a tool the caller cannot use is not the security boundary - the
    dispatcher is - but it stops the model proposing something that can only
    ever be refused, which reads as a broken assistant.
    """
    rank = authority.rank(actor)
    department_scope = authority.feedback_department_scope(actor)
    has_departments = department_scope is ALL or bool(department_scope)

    out: list[dict] = []
    for spec in all_specs():
        if rank > spec.min_rank:
            continue
        if spec.requires_department_scope and not has_departments:
            continue
        out.append(
            {
                "name": spec.name,
                "description": spec.description,
                # Provider-neutral on purpose: `services/chat/provider.py`
                # wraps this into whatever envelope the wire wants. Keeping
                # the shape generic here is what makes the provider a one-file
                # concern rather than a change that reaches the tool layer.
                "parameters": _input_schema(spec.params_model),
            }
        )
    return out


def _input_schema(model: type[BaseModel]) -> dict:
    """Pydantic's JSON schema, shaped the way tool use wants it.

    Enum fields come back as a `$ref` into `$defs`. Those are inlined: a
    self-contained schema is what every provider expects, and indirection is
    one more thing for a model to get wrong.

    `additionalProperties: false` is a statement of intent, not the
    enforcement - `dispatch` re-validates every argument and refuses any the
    tool never declared.

    Optional arguments are expressed the plain JSON-Schema way: left out of
    `required`, typed as the thing they are. The previous shape came from
    Anthropic's strict mode, which wanted every property listed as required
    and optionality expressed as `anyOf: [T, null]`. That is three times the
    JSON for the same meaning, and these schemas are re-sent on every single
    request - roughly 2,000 tokens a call, against a provider budget measured
    per minute. Every field here already has a pydantic default of None, so
    omitting one and passing null are the same thing to `dispatch`.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    schema = _inline_refs(schema, defs)

    schema.pop("title", None)
    schema["additionalProperties"] = False
    properties = schema.get("properties", {})
    for name, prop in list(properties.items()):
        prop.pop("title", None)
        prop.pop("default", None)
        properties[name] = _unwrap_nullable(prop)
    # Only what genuinely has no default. In practice that is nothing today,
    # and pydantic stays the authority either way.
    schema["required"] = sorted(
        name
        for name, field in model.model_fields.items()
        if field.is_required()
    )
    if not schema["required"]:
        schema.pop("required")
    return schema


def _unwrap_nullable(prop: dict) -> dict:
    """`{"anyOf": [T, {"type": "null"}], "description": d}` -> `T` plus `d`.

    Only when there is exactly one non-null branch: a real union of two types
    is left alone, because collapsing it would change what the tool accepts.
    """
    branches = prop.get("anyOf")
    if not isinstance(branches, list):
        return prop
    concrete = [b for b in branches if b.get("type") != "null"]
    if len(concrete) != 1 or len(concrete) == len(branches):
        return prop

    merged = {**concrete[0]}
    for key in ("description", "title"):
        if key in prop and key not in merged:
            merged[key] = prop[key]
    merged.pop("title", None)
    return merged


def _inline_refs(node: Any, defs: dict) -> Any:
    """Replace every `{"$ref": "#/$defs/X"}` with the definition itself."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = defs.get(ref.rsplit("/", 1)[-1], {})
            merged = {**_inline_refs(target, defs), **{k: v for k, v in node.items() if k != "$ref"}}
            merged.pop("title", None)
            return merged
        return {key: _inline_refs(value, defs) for key, value in node.items()}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


def dispatch(ctx: ToolContext, name: str, arguments: dict) -> dict:
    """Run one tool. Returns `{"ok": bool, "result"|"error", "row_count", ...}`.

    Never raises for a tool-level problem - the caller turns this into a
    tool_result block either way.
    """
    started = time.monotonic()
    spec = get(name)

    if spec is None:
        return _failure("UNKNOWN_TOOL", f"There is no tool called {name!r}.", started)

    if authority.rank(ctx.actor) > spec.min_rank:
        return _failure(
            "NOT_PERMITTED",
            "That information is limited to more senior roles. "
            "Tell the user plainly that they cannot see it.",
            started,
        )

    if spec.requires_department_scope and not ctx.has_department_scope:
        return _failure(
            "NOT_PERMITTED",
            "Customer feedback analysis is scoped by rated department and "
            "reaches administrators and appointed department heads only. "
            "Tell the user this is not available to them.",
            started,
        )

    # The published schema says `additionalProperties: false`, so an argument
    # nobody declared means the model is improvising. Pydantic would drop it
    # silently; saying so is what makes the schema and the behaviour agree,
    # and it turns a would-be smuggled `user_id` into a visible refusal.
    unknown = sorted(set(arguments or {}) - set(spec.params_model.model_fields))
    if unknown:
        return _failure(
            "INVALID_ARGUMENTS",
            f"{name} takes no argument called {unknown[0]!r}.",
            started,
        )

    try:
        params = spec.params_model.model_validate(arguments or {})
    except ValidationError as error:
        first = error.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", ())) or "argument"
        return _failure(
            "INVALID_ARGUMENTS", f"{field}: {first.get('msg', 'invalid')}", started
        )

    try:
        result = spec.handler(ctx, params)
    except ToolError as error:
        return _failure(error.code, error.message, started)
    except Exception:  # noqa: BLE001 - the model must not see internals
        # Deliberately swallowed: a stack trace, table name or driver message
        # reaching the model is a leak. The server log keeps the detail.
        return _failure(
            "TOOL_FAILED", "That data could not be read just now.", started
        )

    return {
        "ok": True,
        "result": result,
        "row_count": result.get("count") if isinstance(result, dict) else None,
        "duration_ms": _elapsed(started),
    }


def _failure(code: str, message: str, started: float) -> dict:
    return {
        "ok": False,
        "error": {"code": code, "message": message},
        "row_count": None,
        "duration_ms": _elapsed(started),
    }


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def record_invocation(ctx: ToolContext, name: str, outcome: dict) -> None:
    """One audit row per tool call, in the caller's transaction.

    Arguments are not written here - `chat_tool_calls` holds those. This row
    exists so a chatbot read shows up in the same trail as every other read.
    """
    audit.record(
        ctx.db,
        actor_id=ctx.actor.id,
        action=AuditAction.CHAT_TOOL_INVOKED,
        entity_type=EntityType.CHAT,
        after={
            "tool": name,
            "ok": outcome["ok"],
            "rows": outcome.get("row_count"),
            "error": (outcome.get("error") or {}).get("code"),
        },
        ip_address=ctx.ip_address,
    )
