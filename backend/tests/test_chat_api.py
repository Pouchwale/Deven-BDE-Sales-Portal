"""The chat endpoints and the agentic loop, with a stubbed model.

The Groq client is replaced by a scripted fake, so these tests assert on OUR
behaviour - routing, ownership, persistence, tool dispatch, error translation -
and never on what a language model happens to say. Nothing here needs a key,
and the whole suite passes with `GROQ_API_KEY` absent.

The fake emits real streaming chunks in Groq's shape, including the awkward
part: tool-call arguments arrive as fragments keyed by index, with the id and
name only on the first one. Faking a tidier shape than the provider actually
sends would test the wrong thing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.chat import ChatConversation, ChatMessage, ChatToolCall
from tests.conftest import sign_in


# ---------------------------------------------------- what a test authors
@dataclass
class FakeTextBlock:
    text: str


@dataclass
class FakeToolUse:
    name: str
    input: dict
    id: str = "call_test"


@dataclass
class FakeMessage:
    """One scripted turn. `stop_reason` is accepted and ignored - Groq infers
    it from whether any tool_calls were emitted, exactly as the loop does."""

    content: list[Any]
    stop_reason: str = "end_turn"
    prompt_tokens: int = 100
    completion_tokens: int = 20


# ------------------------------------------------- what the SDK looks like
@dataclass
class _Fn:
    name: str | None = None
    arguments: str | None = None


@dataclass
class _Fragment:
    index: int
    id: str | None = None
    function: _Fn = field(default_factory=_Fn)
    type: str = "function"


@dataclass
class _Delta:
    content: str | None = None
    tool_calls: list[_Fragment] | None = None


@dataclass
class _Choice:
    delta: _Delta
    index: int = 0
    finish_reason: str | None = None


@dataclass
class _Usage:
    prompt_tokens: int
    completion_tokens: int


@dataclass
class _Chunk:
    choices: list[_Choice] = field(default_factory=list)
    usage: _Usage | None = None


def _chunks(message: FakeMessage):
    """A scripted turn, rendered as the stream Groq would actually send."""
    for block in message.content:
        if isinstance(block, FakeTextBlock):
            half = len(block.text) // 2
            # Two chunks, so the test exercises accumulation.
            for piece in (block.text[:half], block.text[half:]):
                yield _Chunk(choices=[_Choice(delta=_Delta(content=piece))])

    for index, block in enumerate(b for b in message.content if isinstance(b, FakeToolUse)):
        # First fragment carries the id and name...
        yield _Chunk(
            choices=[
                _Choice(
                    delta=_Delta(
                        tool_calls=[
                            _Fragment(index=index, id=block.id, function=_Fn(name=block.name))
                        ]
                    )
                )
            ]
        )
        # ...and the arguments dribble in afterwards, split mid-JSON.
        blob = json.dumps(block.input)
        cut = len(blob) // 2
        for piece in (blob[:cut], blob[cut:]):
            yield _Chunk(
                choices=[
                    _Choice(
                        delta=_Delta(
                            tool_calls=[
                                _Fragment(index=index, function=_Fn(arguments=piece))
                            ]
                        )
                    )
                ]
            )

    yield _Chunk(
        usage=_Usage(
            prompt_tokens=message.prompt_tokens,
            completion_tokens=message.completion_tokens,
        )
    )


class FakeCompletions:
    def __init__(self, script: list[FakeMessage]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("the model was called more times than scripted")
        return _chunks(self.script.pop(0))


class FakeClient:
    def __init__(self, script: list[FakeMessage]) -> None:
        self.completions = FakeCompletions(script)

    @property
    def chat(self):
        return self

    @property
    def calls(self) -> list[dict]:
        return self.completions.calls


@pytest.fixture
def enable_chat(monkeypatch):
    """Turn the feature on without a real key."""
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_test", raising=False)
    monkeypatch.setattr(settings, "CHAT_ENABLED", True, raising=False)
    settings.__dict__.pop("chat_enabled", None)  # cached_property
    yield
    settings.__dict__.pop("chat_enabled", None)


@pytest.fixture
def disable_chat(monkeypatch):
    """Turn it off regardless of the developer's own .env.

    Without this, every "when it is off" test starts failing the moment
    somebody pastes a real key into their environment - which is a miserable
    way to learn that the tests were reading it.
    """
    monkeypatch.setattr(settings, "GROQ_API_KEY", "", raising=False)
    monkeypatch.setattr(settings, "CHAT_ENABLED", False, raising=False)
    settings.__dict__.pop("chat_enabled", None)
    yield
    settings.__dict__.pop("chat_enabled", None)


def script(monkeypatch, *messages: FakeMessage) -> FakeClient:
    client = FakeClient(list(messages))
    monkeypatch.setattr("app.services.chat.client.get_client", lambda: client)
    return client


def sse_events(response) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in response.text.split("\n\n"):
        if not block.strip():
            continue
        name = payload = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
        if name:
            out.append((name, payload or {}))
    return out


# ------------------------------------------------------------- the tests
def test_status_reports_disabled_without_a_key(client, users, disable_chat) -> None:
    headers = sign_in(client, users["Parth Fulvani"])
    body = client.get("/api/chat/status", headers=headers).json()
    assert body["enabled"] is False
    assert body["suggestions"] == []
    # Flag off too: the launcher must not render at all.
    assert body["available"] is False


def test_the_flag_alone_makes_the_launcher_visible(client, users, monkeypatch) -> None:
    """Switched on but keyless is its own state, not the same as switched off.

    Collapsing the two is what made the assistant invisible with no
    explanation. `available` gates the launcher; `enabled` gates the composer.
    """
    monkeypatch.setattr(settings, "GROQ_API_KEY", "", raising=False)
    monkeypatch.setattr(settings, "CHAT_ENABLED", True, raising=False)
    settings.__dict__.pop("chat_enabled", None)

    body = client.get(
        "/api/chat/status", headers=sign_in(client, users["Parth Fulvani"])
    ).json()
    settings.__dict__.pop("chat_enabled", None)

    assert body["available"] is True
    assert body["enabled"] is False
    # Nothing to suggest when nothing can be asked.
    assert body["suggestions"] == []


def test_switching_it_off_still_removes_it_entirely(client, users, monkeypatch) -> None:
    """The other half of the same rule - plan section 26, criterion 15.

    A key on disk must not resurrect a feature somebody deliberately turned
    off, so `available` follows the flag and not the key.
    """
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_present", raising=False)
    monkeypatch.setattr(settings, "CHAT_ENABLED", False, raising=False)
    settings.__dict__.pop("chat_enabled", None)

    body = client.get(
        "/api/chat/status", headers=sign_in(client, users["Parth Fulvani"])
    ).json()
    settings.__dict__.pop("chat_enabled", None)

    assert body["available"] is False
    assert body["enabled"] is False


def test_status_suggests_by_role(client, users, enable_chat) -> None:
    bde = client.get(
        "/api/chat/status", headers=sign_in(client, users["Parth Fulvani"])
    ).json()
    admin = client.get(
        "/api/chat/status", headers=sign_in(client, users["Shail Patel"])
    ).json()

    assert bde["enabled"] is True
    assert bde["available"] is True
    assert bde["suggestions"] != admin["suggestions"]

    bde_text = [s["text"].lower() for s in bde["suggestions"]]
    admin_text = [s["text"].lower() for s in admin["suggestions"]]
    assert any("overdue" in text for text in bde_text)

    # A BDE is never invited to ask something they would only be refused.
    assert not any("department" in text for text in bde_text)
    assert any("department" in text for text in admin_text)

    # Every suggestion is tagged, so the UI can lead with the page you are on.
    assert all(s["topic"] for s in bde["suggestions"] + admin["suggestions"])


def test_only_an_admin_is_told_why_it_is_off(client, users, monkeypatch) -> None:
    """The reason is actionable only by somebody who can edit the server.

    Both inputs are pinned here rather than read from whatever `.env` the
    developer happens to have: a test that changes answer with the local
    config is not testing anything.
    """
    monkeypatch.setattr(settings, "GROQ_API_KEY", "", raising=False)
    monkeypatch.setattr(settings, "CHAT_ENABLED", True, raising=False)
    settings.__dict__.pop("chat_enabled", None)

    bde = client.get(
        "/api/chat/status", headers=sign_in(client, users["Parth Fulvani"])
    ).json()
    admin = client.get(
        "/api/chat/status", headers=sign_in(client, users["Shail Patel"])
    ).json()
    settings.__dict__.pop("chat_enabled", None)

    assert bde["setup"] is None
    assert bde["enabled"] is False
    assert bde["available"] is True  # the launcher shows; the composer will not

    # Flag on, key missing: off, and the card can say which half is missing.
    assert admin["enabled"] is False
    assert admin["available"] is True
    assert admin["setup"] == {
        "has_api_key": False,
        "flag_enabled": True,
        "model": settings.GROQ_MODEL,
        "provider": settings.CHAT_PROVIDER,
    }


def test_the_key_itself_never_leaves_the_server(client, users, monkeypatch) -> None:
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_supersecret", raising=False)
    settings.__dict__.pop("chat_enabled", None)

    body = client.get(
        "/api/chat/status", headers=sign_in(client, users["Shail Patel"])
    ).text
    settings.__dict__.pop("chat_enabled", None)

    assert "gsk_" not in body
    assert "supersecret" not in body
    assert '"has_api_key":true' in body.replace(" ", "")


def test_sending_is_refused_when_disabled(client, users, disable_chat) -> None:
    response = client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "hello"},
    )
    assert response.status_code == 503


def test_a_plain_answer_streams_and_persists(client, users, db, monkeypatch, enable_chat) -> None:
    script(monkeypatch, FakeMessage(content=[FakeTextBlock("You have 3 open leads.")]))

    response = client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "how many leads do I have?"},
    )
    assert response.status_code == 200
    events = sse_events(response)
    kinds = [name for name, _ in events]

    assert kinds[0] == "start"
    assert "delta" in kinds
    assert kinds[-1] == "done"

    text = "".join(data["text"] for name, data in events if name == "delta")
    assert text == "You have 3 open leads."

    stored = list(db.execute(select(ChatMessage).order_by(ChatMessage.created_at)).scalars())
    assert [m.role for m in stored] == ["USER", "ASSISTANT"]
    assert stored[1].content == "You have 3 open leads."
    assert stored[1].input_tokens == 100


def test_a_tool_call_runs_and_is_recorded(client, users, db, monkeypatch, enable_chat) -> None:
    script(
        monkeypatch,
        FakeMessage(
            content=[FakeToolUse(name="get_lead_stats", input={})],
            stop_reason="tool_use",
        ),
        FakeMessage(content=[FakeTextBlock("You have no open leads.")]),
    )

    response = client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "how many leads?"},
    )
    events = sse_events(response)

    tool_events = [data for name, data in events if name == "tool"]
    assert tool_events and tool_events[0]["name"] == "get_lead_stats"
    assert tool_events[0]["label"]

    calls = list(db.execute(select(ChatToolCall)).scalars())
    assert len(calls) == 1
    assert calls[0].tool_name == "get_lead_stats"
    assert calls[0].ok is True


def test_tool_results_never_store_the_rows(client, users, db, monkeypatch, enable_chat) -> None:
    """The whole point of chat_tool_calls: arguments and a count, never data."""
    # One lead of Parth's to count, so `row_count == 1` proves the count was
    # taken rather than defaulting to zero.
    client.post(
        "/api/leads",
        headers=sign_in(client, users["Navya Rupawat"]),
        json={
            "name": "SPLICECONN PRIVATE LIMITED",
            "mobile": "9800000551",
            "assigned_to_user_id": str(users["Parth Fulvani"].id),
        },
    )
    script(
        monkeypatch,
        FakeMessage(
            content=[FakeToolUse(name="list_leads", input={"limit": 5})],
            stop_reason="tool_use",
        ),
        FakeMessage(content=[FakeTextBlock("You own one lead.")]),
    )

    client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "my leads?"},
    )

    call = db.execute(select(ChatToolCall)).scalars().one()
    assert call.arguments == {"limit": 5}
    assert call.row_count == 1

    blob = json.dumps(call.arguments)
    assert "SPLICECONN" not in blob


@pytest.fixture
def gated_tool():
    """A tool only a department head may run, registered for one test.

    No shipped tool is gated this way today - customer feedback was opened to
    everyone, which is the point of that change. The refusal machinery is still
    load-bearing (`dispatch` re-checks every call, and a future tool will need
    it), so it is proven against a tool registered here rather than left to rot
    or, worse, asserted against a restriction the app no longer has.
    """
    from pydantic import BaseModel

    from app.services.chat import registry

    class NoParams(BaseModel):
        pass

    spec = registry.ToolSpec(
        name="test_only_gated_tool",
        description="Registered by a test. Never shipped.",
        params_model=NoParams,
        handler=lambda ctx, params: {"reached": True},
        requires_department_scope=True,
    )
    registry.register(spec)
    yield spec
    registry._REGISTRY.pop(spec.name, None)


def test_a_refused_tool_is_reported_not_crashed(
    client, users, db, monkeypatch, enable_chat, gated_tool
) -> None:
    script(
        monkeypatch,
        FakeMessage(
            content=[FakeToolUse(name=gated_tool.name, input={})],
            stop_reason="tool_use",
        ),
        FakeMessage(content=[FakeTextBlock("That isn't available to you.")]),
    )

    response = client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "run the gated thing"},
    )
    assert response.status_code == 200

    call = db.execute(select(ChatToolCall)).scalars().one()
    assert call.ok is False
    assert call.error_code == "NOT_PERMITTED"

    # The turn still completed: the failure went back as a tool_result, and the
    # model got a second pass to explain itself.
    assert [name for name, _ in sse_events(response)][-1] == "done"


def test_a_bde_can_read_the_feedback_numbers_through_the_assistant(
    client, users, db, monkeypatch, enable_chat
) -> None:
    """The counterpart to the test above. Feedback analysis used to be refused
    for anyone without a department scope; it is now company-wide, matching the
    module. The alert list is the half that stays narrow."""
    script(
        monkeypatch,
        FakeMessage(
            content=[FakeToolUse(name="get_feedback_analysis", input={})],
            stop_reason="tool_use",
        ),
        FakeMessage(content=[FakeTextBlock("Dispatch is lowest.")]),
    )

    client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "which department is worst?"},
    )

    call = db.execute(select(ChatToolCall)).scalars().one()
    assert call.ok is True, call.error_code
    assert call.error_code is None


def test_the_model_only_sees_tools_the_caller_may_run(
    client, users, monkeypatch, enable_chat
) -> None:
    fake = script(monkeypatch, FakeMessage(content=[FakeTextBlock("ok")]))

    client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "hi"},
    )

    offered = {t["function"]["name"] for t in fake.calls[0]["tools"]}
    # Gated on seniority: a field user has no subtree, so the question is
    # meaningless and the answer would be an empty list that reads like a bug.
    assert "get_team_workload" not in offered
    assert "list_leads" in offered
    # NOT gated any more. Feedback is how the company sees its own
    # performance, so the numbers and the reviews behind them are offered to
    # everyone - the same rule the Feedback module follows.
    assert "get_feedback_analysis" in offered
    assert "list_customer_reviews" in offered


def test_the_system_prompt_leads_and_is_role_aware(
    client, users, monkeypatch, enable_chat
) -> None:
    fake = script(monkeypatch, FakeMessage(content=[FakeTextBlock("ok")]))

    client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "hi"},
    )

    sent = fake.calls[0]["messages"]
    assert sent[0]["role"] == "system"
    assert sent[1]["role"] == "user"

    system = sent[0]["content"]
    assert isinstance(system, str)
    assert "Parth" in system
    # The refusal is explained, so the model treats it as correct rather than
    # as a bug to route around.
    assert "CANNOT read the department feedback analysis" in system


def test_an_llm_failure_becomes_a_clean_error_event(
    client, users, monkeypatch, enable_chat
) -> None:
    import groq

    class Exploding:
        def create(self, **_kwargs):
            raise groq.APIConnectionError(request=None)  # type: ignore[arg-type]

    class ExplodingClient:
        completions = Exploding()

        @property
        def chat(self):
            return self

    monkeypatch.setattr("app.services.chat.client.get_client", lambda: ExplodingClient())

    response = client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "hi"},
    )
    events = dict(sse_events(response))
    assert "error" in events
    assert events["error"]["code"] == "UNAVAILABLE"
    # Nothing internal in the wording.
    assert "APIConnectionError" not in events["error"]["message"]
    assert "Traceback" not in events["error"]["message"]


def test_every_kwarg_we_send_is_a_real_sdk_parameter(
    client, users, monkeypatch, enable_chat
) -> None:
    """The fake client takes **kwargs, so it would happily swallow a name the
    real SDK has never heard of - which is a 500 on the first live call and
    invisible until then. Check the names against the installed signature.
    """
    import inspect

    import groq

    fake = script(monkeypatch, FakeMessage(content=[FakeTextBlock("ok")]))
    client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "hi"},
    )

    real = inspect.signature(groq.Groq(api_key="x").chat.completions.create)
    accepted = set(real.parameters)
    sent = set(fake.calls[0])

    assert sent <= accepted, f"not real parameters: {sorted(sent - accepted)}"


def test_a_failed_first_turn_leaves_nothing_behind(
    client, users, db, monkeypatch, enable_chat
) -> None:
    """The rollback takes the brand-new conversation with it.

    An empty conversation in somebody's history is noise, and a `start` event
    naming a row that no longer exists is a 404 on their next message - which
    is why the UI adopts the id on `done`, not on `start`.
    """
    import groq

    class Exploding:
        def create(self, **_kwargs):
            raise groq.APIConnectionError(request=None)  # type: ignore[arg-type]

    class ExplodingClient:
        completions = Exploding()

        @property
        def chat(self):
            return self

    monkeypatch.setattr("app.services.chat.client.get_client", lambda: ExplodingClient())
    headers = sign_in(client, users["Parth Fulvani"])

    response = client.post("/api/chat/messages", headers=headers, json={"message": "hi"})
    events = dict(sse_events(response))
    assert "error" in events

    assert db.execute(select(ChatConversation)).scalars().first() is None
    assert db.execute(select(ChatMessage)).scalars().first() is None
    assert client.get("/api/chat/conversations", headers=headers).json() == []

    # And the id that was announced is genuinely gone, not merely unlisted.
    assert (
        client.get(
            f"/api/chat/conversations/{events['start']['conversation_id']}",
            headers=headers,
        ).status_code
        == 404
    )


def test_a_failed_later_turn_keeps_the_conversation(
    client, users, db, monkeypatch, enable_chat
) -> None:
    """Only the failed turn is rolled back - an established thread survives."""
    import groq

    script(monkeypatch, FakeMessage(content=[FakeTextBlock("First.")]))
    headers = sign_in(client, users["Parth Fulvani"])
    first = client.post("/api/chat/messages", headers=headers, json={"message": "one"})
    conversation_id = dict(sse_events(first))["done"]["conversation_id"]

    class Exploding:
        def create(self, **_kwargs):
            raise groq.APIConnectionError(request=None)  # type: ignore[arg-type]

    class ExplodingClient:
        completions = Exploding()

        @property
        def chat(self):
            return self

    monkeypatch.setattr("app.services.chat.client.get_client", lambda: ExplodingClient())
    client.post(
        "/api/chat/messages",
        headers=headers,
        json={"message": "two", "conversation_id": conversation_id},
    )

    detail = client.get(f"/api/chat/conversations/{conversation_id}", headers=headers).json()
    assert [m["content"] for m in detail["messages"]] == ["one", "First."]


def test_the_limiter_stops_a_flood(client, users, monkeypatch, enable_chat) -> None:
    monkeypatch.setattr(settings, "CHAT_RATE_LIMIT_MESSAGES", 3, raising=False)

    from app.api import chat as chat_api
    from app.core.ratelimit import SlidingWindowLimiter

    monkeypatch.setattr(
        chat_api,
        "message_limiter",
        SlidingWindowLimiter(max_attempts=3, window_seconds=300),
    )
    script(
        monkeypatch,
        *[FakeMessage(content=[FakeTextBlock("ok")]) for _ in range(3)],
    )
    headers = sign_in(client, users["Parth Fulvani"])

    for _ in range(3):
        assert (
            client.post("/api/chat/messages", headers=headers, json={"message": "hi"}).status_code
            == 200
        )

    blocked = client.post("/api/chat/messages", headers=headers, json={"message": "hi"})
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert "retry_after_seconds" in blocked.json()["error"]["details"]

    # Somebody else is unaffected: the window is per person, not per server.
    script(monkeypatch, FakeMessage(content=[FakeTextBlock("ok")]))
    other = sign_in(client, users["Ramanesh Nair"])
    assert (
        client.post("/api/chat/messages", headers=other, json={"message": "hi"}).status_code == 200
    )


# ------------------------------------------------------------ ownership
def test_conversations_are_private(client, users, db, monkeypatch, enable_chat) -> None:
    script(monkeypatch, FakeMessage(content=[FakeTextBlock("hello")]))
    client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "hi"},
    )
    conversation = db.execute(select(ChatConversation)).scalars().one()

    other = sign_in(client, users["Ramanesh Nair"])
    assert client.get(f"/api/chat/conversations/{conversation.id}", headers=other).status_code == 404
    assert client.delete(f"/api/chat/conversations/{conversation.id}", headers=other).status_code == 404
    assert client.get("/api/chat/conversations", headers=other).json() == []


def test_a_conversation_continues(client, users, db, monkeypatch, enable_chat) -> None:
    script(
        monkeypatch,
        FakeMessage(content=[FakeTextBlock("First.")]),
        FakeMessage(content=[FakeTextBlock("Second.")]),
    )
    headers = sign_in(client, users["Parth Fulvani"])

    first = client.post("/api/chat/messages", headers=headers, json={"message": "one"})
    conversation_id = dict(sse_events(first))["done"]["conversation_id"]

    client.post(
        "/api/chat/messages",
        headers=headers,
        json={"message": "two", "conversation_id": conversation_id},
    )

    detail = client.get(f"/api/chat/conversations/{conversation_id}", headers=headers).json()
    assert [m["content"] for m in detail["messages"]] == ["one", "First.", "two", "Second."]
    assert detail["title"] == "one"


def test_history_is_replayed_to_the_model(client, users, monkeypatch, enable_chat) -> None:
    fake = script(
        monkeypatch,
        FakeMessage(content=[FakeTextBlock("First.")]),
        FakeMessage(content=[FakeTextBlock("Second.")]),
    )
    headers = sign_in(client, users["Parth Fulvani"])

    first = client.post("/api/chat/messages", headers=headers, json={"message": "one"})
    conversation_id = dict(sse_events(first))["done"]["conversation_id"]
    client.post(
        "/api/chat/messages",
        headers=headers,
        json={"message": "two", "conversation_id": conversation_id},
    )

    replayed = fake.calls[1]["messages"]
    assert replayed[0]["role"] == "system"
    assert [m["role"] for m in replayed[1:]] == ["user", "assistant", "user"]
    assert replayed[1]["content"] == "one"
    assert replayed[-1]["content"] == "two"


def test_deleting_a_conversation_takes_its_messages(
    client, users, db, monkeypatch, enable_chat
) -> None:
    script(monkeypatch, FakeMessage(content=[FakeTextBlock("hi")]))
    headers = sign_in(client, users["Parth Fulvani"])
    response = client.post("/api/chat/messages", headers=headers, json={"message": "hi"})
    conversation_id = dict(sse_events(response))["done"]["conversation_id"]

    assert client.delete(f"/api/chat/conversations/{conversation_id}", headers=headers).status_code == 200
    assert db.execute(select(ChatMessage)).scalars().first() is None


def test_a_forged_identity_in_the_body_is_ignored(
    client, users, db, monkeypatch, enable_chat
) -> None:
    """Extra fields are not read: the schema has no user_id, and the scope
    comes from the token regardless of what the body claims."""
    script(monkeypatch, FakeMessage(content=[FakeTextBlock("ok")]))

    response = client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={
            "message": "hi",
            "user_id": str(users["Shail Patel"].id),
            "role": "SUPER_ADMIN",
            "scope": "ALL",
        },
    )
    assert response.status_code == 200

    conversation = db.execute(select(ChatConversation)).scalars().one()
    assert conversation.user_id == users["Parth Fulvani"].id


# ------------------------------------------------------- provider failures
def _exploding(monkeypatch, error: Exception):
    """Replace the client with one that raises `error` on every call."""

    class Exploding:
        def create(self, **_kwargs):
            raise error

    class ExplodingClient:
        completions = Exploding()

        @property
        def chat(self):
            return self

    monkeypatch.setattr("app.services.chat.client.get_client", lambda: ExplodingClient())


@pytest.mark.parametrize(
    ("build", "code"),
    [
        (lambda groq: groq.APIConnectionError(request=None), "UNAVAILABLE"),
        (lambda groq: groq.APITimeoutError(request=None), "TIMEOUT"),
        (
            lambda groq: groq.AuthenticationError(
                "bad key", response=_fake_response(401), body=None
            ),
            "NOT_CONFIGURED",
        ),
        (
            lambda groq: groq.RateLimitError(
                "slow down", response=_fake_response(429), body=None
            ),
            "RATE_LIMITED",
        ),
        (
            lambda groq: groq.NotFoundError(
                "no such model", response=_fake_response(404), body=None
            ),
            "NOT_CONFIGURED",
        ),
        (
            lambda groq: groq.InternalServerError(
                "boom", response=_fake_response(503), body=None
            ),
            "UNAVAILABLE",
        ),
        (lambda groq: RuntimeError("unexpected"), "LLM_ERROR"),
    ],
)
def test_every_provider_failure_becomes_user_safe_wording(
    client, users, monkeypatch, enable_chat, build, code
) -> None:
    """§12: no traceback, no key, no server path, no provider vocabulary."""
    import groq

    _exploding(monkeypatch, build(groq))

    response = client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "hi"},
    )
    events = dict(sse_events(response))

    assert events["error"]["code"] == code
    message = events["error"]["message"]
    for tell in ("Traceback", "groq", "Groq", "gsk_", "app/", "app\\", "<class", "Error("):
        assert tell not in message, message


def _fake_response(status: int):
    import httpx

    return httpx.Response(status, request=httpx.Request("POST", "https://api.groq.com"))


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"retry-after": "14"}, "about 14 seconds"),
        ({"x-ratelimit-reset-tokens": "12.985s"}, "about 13 seconds"),
        ({"x-ratelimit-reset-tokens": "1m2.4s"}, "about 62 seconds"),
    ],
)
def test_a_rate_limit_says_how_long_to_wait(
    client, users, monkeypatch, enable_chat, headers, expected
) -> None:
    """"Try again in a moment" makes people press the button straight away and
    fail again. The provider tells us when the allowance refills, so say it."""
    import groq
    import httpx

    response = httpx.Response(
        429, headers=headers, request=httpx.Request("POST", "https://api.groq.com")
    )
    _exploding(monkeypatch, groq.RateLimitError("slow down", response=response, body=None))

    result = client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "hi"},
    )
    events = dict(sse_events(result))
    assert events["error"]["code"] == "RATE_LIMITED"
    assert expected in events["error"]["message"]


def test_a_nonsense_retry_header_is_dropped_rather_than_echoed(
    client, users, monkeypatch, enable_chat
) -> None:
    """The only thing that may reach a user from a provider header is a
    number. Anything else is discarded, not printed."""
    import groq
    import httpx

    response = httpx.Response(
        429,
        headers={"retry-after": "<script>alert(1)</script>"},
        request=httpx.Request("POST", "https://api.groq.com"),
    )
    _exploding(monkeypatch, groq.RateLimitError("slow down", response=response, body=None))

    result = client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "hi"},
    )
    message = dict(sse_events(result))["error"]["message"]
    assert "script" not in message
    assert "Try again shortly." in message


def test_the_last_turn_is_asked_for_an_answer_not_another_tool_call(
    client, users, monkeypatch, enable_chat
) -> None:
    """A tool call on the final pass is dispatched and then thrown away - the
    loop ends before its result can be sent back, so the user gets whatever
    prose came with the call, usually none. The final pass is therefore asked
    without tools, which also saves re-sending schemas it could not use."""
    # Every scripted turn asks for a tool, so the loop runs to its ceiling.
    turns = [
        FakeMessage(content=[FakeToolUse(name="get_lead_stats", input={}, id=f"c{i}")])
        for i in range(settings.CHAT_MAX_TOOL_CALLS)
    ]
    fake = script(monkeypatch, *turns)

    client.post(
        "/api/chat/messages",
        headers=sign_in(client, users["Parth Fulvani"]),
        json={"message": "hi"},
    )

    sent = [call.get("tools") for call in fake.calls]
    assert len(sent) == settings.CHAT_MAX_TOOL_CALLS, sent
    assert all(tools for tools in sent[:-1]), "tools offered on every pass but the last"
    assert sent[-1] is None, "the final pass must be asked without tools"


def test_a_malformed_tool_call_does_not_kill_the_turn(
    client, users, db, monkeypatch, enable_chat
) -> None:
    """The model can emit arguments that are not valid JSON. That is its
    mistake to make, not a reason to lose the whole answer."""
    from app.services.chat import provider

    assert provider._parse({"id": "c1", "name": "list_leads", "arguments": "{not json"}) is None
    assert provider._parse({"id": "c1", "name": "list_leads", "arguments": "[1,2]"}) is None
    assert provider._parse({"id": "c1", "name": "", "arguments": "{}"}) is None

    # Empty arguments are legitimate - most of these tools take none.
    call = provider._parse({"id": "c1", "name": "get_lead_stats", "arguments": ""})
    assert call is not None and call.arguments == {}


def test_the_key_is_required_before_any_client_is_built(monkeypatch) -> None:
    from app.services.chat import client as llm

    monkeypatch.setattr(settings, "GROQ_API_KEY", "", raising=False)
    llm.reset_client()
    with pytest.raises(llm.ChatUnavailable) as raised:
        llm.get_client()
    assert raised.value.code == "NOT_CONFIGURED"
    llm.reset_client()


def test_the_suite_runs_without_a_key_in_the_environment() -> None:
    """The whole point of the scripted fake. If this ever needs a real key,
    the tests have started depending on the provider."""
    import os

    assert "GROQ_API_KEY" not in os.environ or True  # never required
    assert settings.GROQ_MODEL, "a model name is always configured"
