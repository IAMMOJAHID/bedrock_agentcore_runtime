from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import tools


def ctx(session_id: str, user_text: str = "due in three days", actor_id: str | None = None):
    """Minimal stand-in for ADK's ToolContext (session.id/user_id, user_content, session.events)."""
    content = SimpleNamespace(parts=[SimpleNamespace(text=user_text)])
    session = SimpleNamespace(id=session_id, user_id=actor_id or f"actor-{session_id}", events=[])
    return SimpleNamespace(session=session, user_content=content)


FUTURE = (date.today() + timedelta(days=3)).isoformat()


def test_tasks_are_actor_scoped():
    a1 = ctx("s-a1", actor_id="USER-A")
    a2 = ctx("s-a2", actor_id="USER-A")  # same actor, another session
    b = ctx("s-b1", actor_id="USER-B")
    assert tools.create_task("Task of A", FUTURE, "three days", a1)["task"]["task_id"] == "TASK-001"
    assert tools.list_tasks(a2)["count"] == 1  # same actor sees own tasks across sessions (same process)
    assert tools.list_tasks(b)["count"] == 0   # another actor never does
    tools.reset_actor_store("USER-A")           # simulated restart: in-process tasks are gone
    assert tools.list_tasks(a1)["count"] == 0


def test_validation_errors_returned_as_data():
    c = ctx("session-validation")
    res = tools.create_task("", FUTURE, "three days", c)
    assert res == {"status": "error", "error_type": "validation_error", "message": "A task title is required."}
    assert tools.create_task("x", "next friday", "three days", c)["error_type"] == "validation_error"
    assert tools.complete_task("TASK-999", c)["error_type"] == "not_found"


def test_complete_flow():
    c = ctx("session-complete")
    tools.create_task("Review", FUTURE, "three days", c)
    res = tools.complete_task("TASK-001", c)
    assert res["status"] == "success" and res["task"]["status"] == "completed"
    assert tools.list_tasks(c, "pending")["count"] == 0


def test_simulated_failure_raises_and_handler_converts():
    c = ctx("session-fail")
    tools.set_simulated_failures("session-fail", ["create_task"])
    with pytest.raises(tools.SimulatedToolFailure):
        tools.create_task("Review", FUTURE, "three days", c)
    # nothing was created
    assert tools.list_tasks(c)["count"] == 0
    err = tools.handle_tool_error(
        SimpleNamespace(name="create_task"), {"title": "Review"}, c, tools.SimulatedToolFailure("boom")
    )
    assert err["status"] == "error" and err["error_type"] == "tool_failure"
    assert "NOT performed" in err["message"]
    tools.set_simulated_failures("session-fail", [])
    assert tools.create_task("Review", FUTURE, "three days", c)["status"] == "success"


def test_due_date_must_be_stated_by_user():
    c = ctx("session-grounding", user_text="Create a task to call the vendor.")
    res = tools.create_task("Call the vendor", FUTURE, "tomorrow", c)
    assert res["error_type"] == "missing_due_date"
    assert tools.list_tasks(c)["count"] == 0


def test_due_date_phrase_from_earlier_user_turn_is_accepted():
    earlier = SimpleNamespace(author="user", content=SimpleNamespace(parts=[SimpleNamespace(text="It is due next Friday")]))
    c = ctx("session-grounding-2", user_text="yes please create it")
    c.session.events = [earlier]
    assert tools.create_task("Call the vendor", FUTURE, "next Friday", c)["status"] == "success"
