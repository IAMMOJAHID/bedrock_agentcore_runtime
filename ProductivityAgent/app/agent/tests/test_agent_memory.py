import asyncio

import agent


def test_render_memory_context_states():
    ok = agent.render_memory_context(
        "USER-A", "YES", "ok", [{"text": "Prefers tables", "created_at": "2026-11-22T10:00:00"}]
    )
    assert "[2026-11-22] Prefers tables" in ok and "saved to memory: YES" in ok
    assert "NO stored preferences" in agent.render_memory_context("USER-B", "YES", "empty", [])
    assert "UNAVAILABLE" in agent.render_memory_context("USER-A", "NO", "unavailable", [])


def test_history_replayed_into_new_adk_session():
    async def go():
        history = [("USER", "Create a task for architecture review", None),
                   ("ASSISTANT", "Which due date?", None)]
        n = await agent.ensure_session("USER-A", "replay-session", history)
        s = await agent.session_service.get_session(
            app_name=agent.APP_NAME, user_id="USER-A", session_id="replay-session"
        )
        again = await agent.ensure_session("USER-A", "replay-session", history)  # already local
        await agent.drop_local_session("USER-A", "replay-session")
        return n, [(e.author, e.content.parts[0].text) for e in s.events], again

    n, events, again = asyncio.run(go())
    assert n == 2 and again == 0
    assert events == [
        ("user", "Create a task for architecture review"),
        ("productivity_assistant", "Which due date?"),
    ]


def test_split_inline_thinking():
    visible, thoughts = agent.split_inline_thinking("<thinking>check memory</thinking>\nI cannot access it now.")
    assert visible == "I cannot access it now." and thoughts == ["check memory"]
    assert agent.split_inline_thinking("plain answer") == ("plain answer", [])


def test_reply_cleanup_and_grounding():
    visible, _ = agent.split_inline_thinking("<reply>Done. This message was saved to memory: YES. Bye.</reply>")
    assert "<reply>" not in visible and "saved to memory" not in visible and "Done." in visible
    calls = [{"tool": "list_tasks", "result": {"tasks": [{"task_id": "TASK-001"}]}}]
    assert agent.ungrounded_task_ids("TASK-001 is pending", calls) == set()
    assert agent.ungrounded_task_ids("TASK-001 and TASK-002", calls) == {"TASK-002"}
    assert agent.ungrounded_task_ids("TASK-002 is pending", []) == {"TASK-002"}
