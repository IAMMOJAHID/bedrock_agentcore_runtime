"""ADK function tools exposed to the agent.

Every tool returns a structured dict with ``status`` set to ``"success"`` or
``"error"``. Expected business errors (validation, unknown id) are caught here
and returned as data so the model can explain them. Unexpected exceptions
(including the simulated backend outage used for demo scenario 6) are allowed
to propagate and are converted into an error payload by
``handle_tool_error`` (wired as the agent's ``on_tool_error_callback``), so a
failing tool never crashes the agent loop.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

from google.adk.tools import ToolContext

from task_store import TaskError, TaskStore

logger = logging.getLogger("productivity_agent.tools")

TOOL_NAMES = ("create_task", "list_tasks", "complete_task")

# Returned with write results: small models otherwise tend to answer "...and show
# my tasks" from memory instead of calling list_tasks (seen with Nova Lite).
NEXT_STEP_NOTE = (
    "The task list has changed. If the user's request also asks to see, show or "
    "list tasks, call list_tasks now - do not list tasks from memory."
)

_stores: dict[str, TaskStore] = {}
_simulated_failures: dict[str, set[str]] = {}
_registry_lock = threading.Lock()


class SimulatedToolFailure(RuntimeError):
    """Raised when failure injection is enabled for a tool (demo scenario 6)."""


# --------------------------------------------------------------------------- #
# Actor/session-scoped state helpers
# --------------------------------------------------------------------------- #
def get_store(actor_id: str) -> TaskStore:
    """Task store for one actor (user). In-process only: NOT persistent.

    V2 keys tasks by actor so "my tasks" works across a user's sessions while the
    same process is alive (local dev). On AgentCore Runtime each session runs in
    its own microVM, so tasks still do not survive across sessions or restarts.
    AgentCore Memory is deliberately NOT used as a task database.
    """
    with _registry_lock:
        store = _stores.get(actor_id)
        if store is None:
            store = _stores[actor_id] = TaskStore()
        return store


def reset_actor_store(actor_id: str) -> None:
    """Drop an actor's in-process tasks (used to simulate a process restart)."""
    with _registry_lock:
        _stores.pop(actor_id, None)


def set_simulated_failures(session_id: str, tool_names: list[str] | set[str] | None) -> None:
    """Enable failure injection for the given tools in a session ([] disables it)."""
    names = {n for n in (tool_names or []) if n in TOOL_NAMES}
    with _registry_lock:
        if names:
            _simulated_failures[session_id] = names
        else:
            _simulated_failures.pop(session_id, None)


def _session_id(tool_context: ToolContext) -> str:
    return tool_context.session.id


def _actor_id(tool_context: ToolContext) -> str:
    return tool_context.session.user_id


def _maybe_fail(tool_name: str, session_id: str) -> None:
    if tool_name in _simulated_failures.get(session_id, set()):
        raise SimulatedToolFailure(
            f"Task backend unavailable while executing {tool_name} (simulated failure)."
        )


def _error(err: TaskError) -> dict[str, Any]:
    return {"status": "error", "error_type": err.error_type, "message": str(err)}


_WORD = re.compile(r"[a-z0-9]+")


def _user_texts(tool_context: ToolContext) -> list[str]:
    """All user-authored text in this session, including the current message."""
    texts: list[str] = []
    for content in [tool_context.user_content] + [
        e.content for e in (getattr(tool_context.session, "events", None) or []) if e.author == "user"
    ]:
        for part in (getattr(content, "parts", None) or []):
            if getattr(part, "text", None):
                texts.append(part.text)
    return texts


def _phrase_stated_by_user(phrase: str | None, tool_context: ToolContext) -> bool:
    """Grounding guard: every word of the date phrase must appear in what the user wrote.

    Stops the model inventing a due date the user never gave (assignment rule:
    "avoid fabricating task information"). Deterministic, so it does not rely on
    the model following the prompt.
    """
    words = _WORD.findall((phrase or "").lower())
    if not words:
        return False
    user_words = set(_WORD.findall(" ".join(_user_texts(tool_context)).lower()))
    return all(w in user_words for w in words)


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
def create_task(
    title: str, due_date: str, user_due_date_phrase: str, tool_context: ToolContext
) -> dict[str, Any]:
    """Create a new task with a title and a due date.

    Args:
        title: Short description of the task, e.g. "Complete the architecture review".
        due_date: Due date in ISO format YYYY-MM-DD. Convert relative dates such as
            "Friday" or "tomorrow" to an absolute date before calling.
        user_due_date_phrase: The due-date words exactly as the USER wrote them,
            e.g. "Friday", "next Monday", "2026-11-25". Never make this up; if the
            user gave no due date, do not call this tool - ask them instead.

    Returns:
        On success: {"status": "success", "task": {...}} including the new task_id.
        On failure: {"status": "error", "error_type": ..., "message": ...}.
    """
    sid = _session_id(tool_context)
    _maybe_fail("create_task", sid)
    if not _phrase_stated_by_user(user_due_date_phrase, tool_context):
        logger.info("create_task rejected: due date phrase %r not stated by user", user_due_date_phrase)
        return {
            "status": "error",
            "error_type": "missing_due_date",
            "message": (
                "The user has not specified a due date for this task. No task was created. "
                "Ask the user when the task is due."
            ),
        }
    try:
        task = get_store(_actor_id(tool_context)).create(title, due_date)
    except TaskError as err:
        logger.info("create_task rejected: %s", err)
        return _error(err)
    logger.info("create_task ok: %s", task.task_id)
    return {
        "status": "success",
        "message": f"Task {task.task_id} created.",
        "task": task.to_dict(),
        "note": NEXT_STEP_NOTE,
    }


def list_tasks(tool_context: ToolContext, status: str = "pending") -> dict[str, Any]:
    """List tasks, filtered by status.

    Args:
        status: Which tasks to return: "pending" (default for "my tasks"),
            "completed", or "all".

    Returns:
        On success: {"status": "success", "filter": ..., "count": N, "tasks": [...]}.
        On failure: {"status": "error", "error_type": ..., "message": ...}.
    """
    sid = _session_id(tool_context)
    _maybe_fail("list_tasks", sid)
    try:
        tasks = get_store(_actor_id(tool_context)).list(status)
    except TaskError as err:
        return _error(err)
    return {
        "status": "success",
        "filter": (status or "pending").lower(),
        "count": len(tasks),
        "tasks": [t.to_dict() for t in tasks],
    }


def complete_task(task_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Mark an existing task as completed.

    Args:
        task_id: The task identifier, e.g. "TASK-001".

    Returns:
        On success: {"status": "success", "task": {...}} with status "completed".
        On failure: {"status": "error", "error_type": "not_found" | "validation_error", ...}.
    """
    sid = _session_id(tool_context)
    _maybe_fail("complete_task", sid)
    try:
        task, changed = get_store(_actor_id(tool_context)).complete(task_id)
    except TaskError as err:
        logger.info("complete_task rejected: %s", err)
        return _error(err)
    message = (
        f"Task {task.task_id} marked as completed."
        if changed
        else f"Task {task.task_id} was already completed."
    )
    return {
        "status": "success",
        "message": message,
        "changed": changed,
        "task": task.to_dict(),
        "note": NEXT_STEP_NOTE,
    }


# --------------------------------------------------------------------------- #
# Framework-level error handler
# --------------------------------------------------------------------------- #
def handle_tool_error(tool, args: dict[str, Any], tool_context, error: Exception) -> dict[str, Any] | None:
    """ADK ``on_tool_error_callback``: turn unexpected tool exceptions into data.

    Returning a dict makes ADK send it to the model as the tool's response
    instead of aborting the invocation.
    """
    import asyncio

    if isinstance(error, asyncio.CancelledError):
        return None
    logger.error("Tool %s failed with args=%s: %r", getattr(tool, "name", tool), args, error)
    return {
        "status": "error",
        "error_type": "tool_failure",
        "message": (
            f"The {getattr(tool, 'name', 'tool')} operation failed: {error}. "
            "The operation was NOT performed."
        ),
    }


ALL_TOOLS = [create_task, list_tasks, complete_task]
