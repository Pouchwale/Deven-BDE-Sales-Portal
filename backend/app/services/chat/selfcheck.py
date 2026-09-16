"""A live check against the real Groq API.

    cd backend && python -m app.services.chat.selfcheck

**This consumes Groq API usage.** Every other assistant test in this repo runs
against a scripted fake and never leaves the machine; this one exists for the
handful of things a fake cannot tell you:

  1. `GROQ_API_KEY` is present and Groq accepts it.
  2. `GROQ_MODEL` names a model this account can actually reach.
  3. A minimal completion succeeds.
  4. The generated tool schemas are accepted rather than 400'd.
  5. Tool calling works end to end - the model asks for a real tool, the real
     dispatcher runs it under a real scope, and the answer comes back.
  6. Failures are still translated into user-safe wording.

The key is never printed, and no portal row is printed either - only counts.
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from app.core import authority
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.org import User
from app.services.chat import client as llm
from app.services.chat import prompt as prompt_builder
from app.services.chat import provider
from app.services.chat import tools as chat_tools
from app.services.chat.registry import ToolContext

TOOL_QUESTION = "How many open leads do I have right now?"


def main() -> int:
    print("This makes real Groq API calls and consumes usage.\n")

    # ---------------------------------------------------------- 1, 2. config
    if not settings.GROQ_API_KEY.strip():
        print("FAIL  GROQ_API_KEY is not set in backend/.env")
        return 1
    if not settings.CHAT_ENABLED:
        print("FAIL  CHAT_ENABLED is false")
        return 1
    if not settings.GROQ_MODEL.strip():
        print("FAIL  GROQ_MODEL is empty")
        return 1

    print(f"provider   {settings.CHAT_PROVIDER}")
    print(f"model      {settings.GROQ_MODEL}")
    print(f"key        present ({len(settings.GROQ_API_KEY.strip())} chars, not shown)")

    with SessionLocal() as db:
        actor = (
            db.execute(
                select(User).where(User.is_active.is_(True)).order_by(User.created_at)
            )
            .scalars()
            .first()
        )
        if actor is None:
            print("FAIL  no users. Run `python -m app.seeds.seed` first.")
            return 1

        scope = authority.visible_user_ids(db, actor)
        department_scope = authority.feedback_department_scope(actor)
        system = prompt_builder.build_system(actor, scope, department_scope)
        tools = provider.tool_schemas(actor)

        print(f"acting as  {actor.name} ({actor.role})")
        print(f"tools      {len(tools)} offered")
        print(f"system     {len(system)} chars\n")

        # ------------------------------------------------- 3. plain completion
        try:
            turn = provider.stream_turn(
                system=system,
                history=[{"role": "user", "content": "Reply with the single word: ready"}],
                tools=[],
            )
            text = "".join(turn.text_deltas())
        except Exception as error:  # noqa: BLE001 - reporting is the job here
            print(f"FAIL  plain completion: {llm.translate(error).code}")
            print(f"      ({type(error).__name__})")
            return 1

        print(f"[1] completion   ok — {text.strip()[:40]!r}")

        # --------------------------------------- 4, 5. tool schemas + calling
        try:
            turn = provider.stream_turn(
                system=system,
                history=[{"role": "user", "content": TOOL_QUESTION}],
                tools=tools,
            )
            for _ in turn.text_deltas():
                pass
            reply = turn.reply
        except Exception as error:  # noqa: BLE001
            failure = llm.translate(error)
            print(f"FAIL  tool schemas rejected: {failure.code} ({type(error).__name__})")
            return 1

        print(f"[2] schemas      ok — {len(tools)} accepted")

        if not reply.wants_tools:
            print("WARN [3] tool calling — the model answered without a tool.")
            print(f"      It said: {reply.text.strip()[:90]!r}")
            print(f"      Try a model with stronger tool use than {settings.GROQ_MODEL!r}.")
            return 2

        names = ", ".join(call.name for call in reply.tool_calls)
        print(f"[3] tool calling ok — asked for: {names}")

        # ------------------------------ the real dispatcher, the real scope
        ctx = ToolContext(
            db=db,
            actor=actor,
            scope=scope,
            department_scope=department_scope,
            ip_address=None,
        )
        history = [
            {"role": "user", "content": TOOL_QUESTION},
            provider.assistant_turn(reply),
        ]
        for call in reply.tool_calls:
            outcome = chat_tools.dispatch(ctx, call.name, call.arguments)
            state = "ok" if outcome["ok"] else outcome["error"]["code"]
            print(f"    {call.name:32} {state}  rows={outcome.get('row_count')}")
            body = (
                f"<portal_data>{outcome['result']}</portal_data>"
                if outcome["ok"]
                else f"<portal_error>{outcome['error']['message']}</portal_error>"
            )
            history.append(provider.tool_result(call, body))
        db.rollback()  # the audit rows this wrote are not wanted from a check

        # --------------------------------------------- 6. the answer comes back
        try:
            turn = provider.stream_turn(system=system, history=history, tools=tools)
            answer = "".join(turn.text_deltas())
        except Exception as error:  # noqa: BLE001
            print(f"FAIL  second pass: {llm.translate(error).code}")
            return 1

        print(f"[4] answer       ok — {answer.strip()[:100]!r}")

    # ------------------------------------------------- 7. error handling
    sample = llm.translate(RuntimeError("connection to db://user:pw@host failed"))
    leaked = any(t in sample.message for t in ("db://", "user:pw", "RuntimeError"))
    print(f"[5] error safety {'FAIL — leaked internals' if leaked else 'ok'}")

    print("\nPASS  Groq is configured and tool calling works end to end.")
    return 1 if leaked else 0


if __name__ == "__main__":
    sys.exit(main())
