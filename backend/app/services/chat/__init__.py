"""The in-portal AI assistant.

The whole design rests on one invariant, enforced here rather than asked for
in a prompt:

    No tool accepts an actor, a user id, or a visibility scope.

The caller's scope is built server-side from the authenticated session and
handed to every tool in a `ToolContext`. A request for somebody else's data is
therefore not "denied" - it is inexpressible, because no tool schema has an
argument that could carry it.

See docs/CHATBOT_IMPLEMENTATION_PLAN.md.
"""
