from datetime import date

import pytest

from task_store import TaskNotFoundError, TaskStore, TaskValidationError, parse_due_date

TODAY = date(2026, 11, 18)


def test_create_assigns_sequential_ids():
    store = TaskStore()
    t1 = store.create("Architecture review", "2026-11-20", today=TODAY)
    t2 = store.create("Write README", "2026-11-21", today=TODAY)
    assert (t1.task_id, t2.task_id) == ("TASK-001", "TASK-002")
    assert t1.status == "pending"


@pytest.mark.parametrize("title", ["", "   ", None])
def test_create_requires_title(title):
    with pytest.raises(TaskValidationError, match="title is required"):
        TaskStore().create(title, "2026-11-20", today=TODAY)


@pytest.mark.parametrize("bad", ["Friday", "2026-13-01", "20/11/2026", "", None])
def test_invalid_dates_rejected(bad):
    with pytest.raises(TaskValidationError):
        parse_due_date(bad, today=TODAY)


def test_past_date_rejected():
    with pytest.raises(TaskValidationError, match="in the past"):
        parse_due_date("2026-11-17", today=TODAY)


def test_today_is_allowed():
    assert parse_due_date("2026-11-18", today=TODAY) == TODAY


def test_list_filters_and_sorts():
    store = TaskStore()
    store.create("Later", "2026-12-01", today=TODAY)
    store.create("Sooner", "2026-11-19", today=TODAY)
    store.complete("TASK-001")
    assert [t.title for t in store.list("pending")] == ["Sooner"]
    assert [t.title for t in store.list("completed")] == ["Later"]
    assert [t.title for t in store.list("all")] == ["Sooner", "Later"]
    with pytest.raises(TaskValidationError, match="Unknown status"):
        store.list("archived")


def test_complete_unknown_and_idempotent():
    store = TaskStore()
    store.create("Review", "2026-11-20", today=TODAY)
    with pytest.raises(TaskNotFoundError, match="TASK-999"):
        store.complete("TASK-999")
    task, changed = store.complete("task-001")  # case-insensitive
    assert changed and task.status == "completed" and task.completed_at
    _, changed_again = store.complete("TASK-001")
    assert changed_again is False
