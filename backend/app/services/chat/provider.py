"""The provider boundary.

Everything that knows what the model's wire format looks like lives here and
in `client.py`. `session.py` runs the loop in the portal's own vocabulary -
a system prompt, a list of turns, a set of tools, some tool calls to run -
and never imports an SDK.

That split is the point. Swapping Groq for something else means rewriting this
file and `client.py`; the loop, the tools, the projections, the authorization
gates and the audit trail do not move.

Groq speaks the OpenAI chat-completions dialect:

  * tools are `{"type": "function", "function": {name, description, parameters}}`
  * the model answers with `message.tool_calls[]`, arguments as a JSON *string*
  * each result goes back as its own `{"role": "tool", "tool_call_id": ...}`
    message, contiguous and immediately after the assistant turn that asked

Which is a different shape from the tool registry's, deliberately: the
registry stays provider-neutral so this is the only file that has to change.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.core.config import settings
from app.models.org import User
from app.services.chat import client as llm
from app.services.chat import tools as chat_tools

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool the model wants run, already parsed."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Reply:
    """One completed model turn, in terms the loop understands."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def tool_schemas(actor: User) -> list[dict]:
    """The tools this caller may run, in the provider's shape.

    The filtering itself is the registry's - and it is not the security
    boundary either way; `dispatch` re-checks. This only translates.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["parameters"],
            },
        }
        for spec in chat_tools.schemas_for(actor)
    ]


def build_messages(system: str, history: list[dict]) -> list[dict]:
    """System prompt plus the conversation so far."""
    return [{"role": "system", "content": system}, *history]


def assistant_turn(reply: Reply) -> dict:
    """The assistant message to append before sending tool results back.

    Must carry the same `tool_calls` ids the results will reference, or the
    next request is rejected for referring to a call that never happened.
    """
    message: dict[str, Any] = {"role": "assistant", "content": reply.text or None}
    if reply.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, default=str),
                },
            }
            for call in reply.tool_calls
        ]
    return message


def tool_result(call: ToolCall, payload: str) -> dict:
    """One tool's output, addressed back to the call that asked for it."""
    return {"role": "tool", "tool_call_id": call.id, "content": payload}


class StreamedTurn:
    """A turn in flight.

    Drain `text_deltas()`, then read `reply`. Deliberately the same shape the
    loop used before, so switching providers did not reshape the loop.
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._reply = Reply()
        self._done = False

    def text_deltas(self) -> Iterator[str]:
        """Yield visible text as it arrives, accumulating everything else."""
        # Tool-call arguments stream in fragments, keyed by index rather than
        # id - the id only appears on the first fragment. So they are
        # assembled here and parsed once at the end.
        partial: dict[int, dict[str, Any]] = {}

        for chunk in self._stream:
            if not chunk.choices:
                # The final usage-only frame carries no choices.
                self._absorb_usage(chunk)
                continue

            delta = chunk.choices[0].delta

            text = getattr(delta, "content", None)
            if text:
                self._reply.text += text
                yield text

            for fragment in getattr(delta, "tool_calls", None) or []:
                slot = partial.setdefault(
                    fragment.index, {"id": "", "name": "", "arguments": ""}
                )
                if fragment.id:
                    slot["id"] = fragment.id
                function = getattr(fragment, "function", None)
                if function is not None:
                    if function.name:
                        slot["name"] = function.name
                    if function.arguments:
                        slot["arguments"] += function.arguments

            self._absorb_usage(chunk)

        self._reply.tool_calls = [
            parsed
            for _, slot in sorted(partial.items())
            if (parsed := _parse(slot)) is not None
        ]
        self._done = True

    def _absorb_usage(self, chunk: Any) -> None:
        usage = getattr(chunk, "usage", None) or getattr(
            getattr(chunk, "x_groq", None), "usage", None
        )
        if usage is None:
            return
        self._reply.input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        self._reply.output_tokens = getattr(usage, "completion_tokens", 0) or 0

    @property
    def reply(self) -> Reply:
        if not self._done:
            raise RuntimeError("read `reply` only after draining `text_deltas()`")
        return self._reply


def _parse(slot: dict[str, Any]) -> ToolCall | None:
    """Turn one assembled fragment into a call, or drop it.

    A malformed `arguments` string is the model's mistake, not an exception:
    the call is dropped and the loop carries on with whatever else it asked
    for. Raising here would lose a whole turn over one bad JSON blob.
    """
    name = slot.get("name") or ""
    if not name:
        return None

    raw = (slot.get("arguments") or "").strip() or "{}"
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError:
        logger.info("Model sent unparseable arguments for %s; dropping the call", name)
        return None

    if not isinstance(arguments, dict):
        logger.info("Model sent non-object arguments for %s; dropping the call", name)
        return None

    return ToolCall(id=slot.get("id") or f"call_{name}", name=name, arguments=arguments)


def stream_turn(*, system: str, history: list[dict], tools: list[dict]) -> StreamedTurn:
    """Send one request and hand back the turn in flight."""
    client = llm.get_client()
    stream = client.chat.completions.create(
        model=settings.GROQ_MODEL,
        messages=build_messages(system, history),  # type: ignore[arg-type]
        tools=tools or None,  # type: ignore[arg-type]
        temperature=settings.CHAT_TEMPERATURE,
        max_tokens=settings.CHAT_MAX_TOKENS,
        stream=True,
    )
    return StreamedTurn(stream)
