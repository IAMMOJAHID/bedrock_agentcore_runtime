"""In-memory task store for the Personal Productivity Assistant.

Pure domain logic with no agent-framework dependencies, so it can be unit tested
in isolation. One ``TaskStore`` exists per conversation session (see
``tools.get_store``). On AgentCore Runtime every session runs in its own
microVM, so the store is naturally session-scoped there too. Data is lost when
the session ends; cross-session persistence is out of scope for this assignment.
"""

from __future__ import annotations

import os
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
VALID_STATUS_FILTERS = ("pending", "completed", "all")
MAX_TITLE_LENGTH = 200


class TaskError(Exception):
    """Base class for expected, user-facing task errors."""

    error_type = "task_error"


class TaskValidationError(TaskError):
    """Input failed validation (missing title, bad date, bad filter...)."""

    error_type = "validation_error"


class TaskNotFoundError(TaskError):
    """The requested task identifier does not exist."""

    error_type = "not_found"


@dataclass
class Task:
    task_id: str
    title: str
    due_date: str  # ISO-8601 date, YYYY-MM-DD
    status: str
    created_at: str
    completed_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def agent_timezone() -> ZoneInfo:
    """Timezone used to interpret 'today' (env AGENT_TIMEZONE, default UTC)."""
    name = os.environ.get("AGENT_TIMEZONE", "UTC")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def local_today() -> date:
    return datetime.now(agent_timezone()).date()


def parse_due_date(raw: str | None, today: date | None = None) -> date:
    """Validate a due date string and return it as a ``date``.

    Only ISO dates (YYYY-MM-DD) are accepted. The LLM resolves relative phrases
    such as "Friday" to a concrete date before calling the tool, so validation
    here stays deterministic.
    """
    today = today or local_today()
    if raw is None or not str(raw).strip():
        raise TaskValidationError("A due date is required (format YYYY-MM-DD).")
    try:
        parsed = date.fromisoformat(str(raw).strip())
    except ValueError:
        raise TaskValidationError(
            f"'{raw}' is not a valid date. Use the format YYYY-MM-DD, e.g. 2026-11-20."
        ) from None
    if parsed < today:
        raise TaskValidationError(
            f"Due date {parsed.isoformat()} is in the past (today is {today.isoformat()})."
        )
    return parsed


class TaskStore:
    """Thread-safe in-memory task repository with sequential TASK-NNN ids."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._next_id = 1
        self._lock = threading.Lock()

    def create(self, title: str | None, due_date: str | None, today: date | None = None) -> Task:
        title = (title or "").strip()
        if not title:
            raise TaskValidationError("A task title is required.")
        if len(title) > MAX_TITLE_LENGTH:
            raise TaskValidationError(
                f"Task title is too long ({len(title)} chars, max {MAX_TITLE_LENGTH})."
            )
        due = parse_due_date(due_date, today)
        with self._lock:
            task_id = f"TASK-{self._next_id:03d}"
            self._next_id += 1
            task = Task(
                task_id=task_id,
                title=title,
                due_date=due.isoformat(),
                status=STATUS_PENDING,
                created_at=_now_iso(),
            )
            self._tasks[task_id] = task
        return task

    def list(self, status: str | None = "pending") -> list[Task]:
        status = (status or "pending").strip().lower()
        if status not in VALID_STATUS_FILTERS:
            raise TaskValidationError(
                f"Unknown status filter '{status}'. Use one of: {', '.join(VALID_STATUS_FILTERS)}."
            )
        with self._lock:
            tasks = list(self._tasks.values())
        if status != "all":
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: (t.due_date, t.task_id))

    def complete(self, task_id: str | None) -> tuple[Task, bool]:
        """Mark a task completed. Returns (task, changed); changed=False if already done."""
        normalized = (task_id or "").strip().upper()
        if not normalized:
            raise TaskValidationError("A task identifier is required, e.g. TASK-001.")
        with self._lock:
            task = self._tasks.get(normalized)
            if task is None:
                raise TaskNotFoundError(f"Task {normalized} does not exist.")
            if task.status == STATUS_COMPLETED:
                return task, False
            task.status = STATUS_COMPLETED
            task.completed_at = _now_iso()
            return task, True
