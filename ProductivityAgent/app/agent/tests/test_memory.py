from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError

from memory import MemoryGateway, preference_namespace, validate_actor_id

T0 = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)


class FakeClient:
    """In-memory stand-in for the bedrock-agentcore data-plane client."""

    def __init__(self, records=None, fail=None):
        self.events, self.records, self.fail = [], records or {}, fail or set()

    def _maybe_fail(self, op):
        if op in self.fail:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, op)

    def create_event(self, **kw):
        self._maybe_fail("create_event")
        self.events.append(kw)
        return {"event": {"eventId": f"evt-{len(self.events)}"}}

    def list_events(self, **kw):
        self._maybe_fail("list_events")
        evs = [e for e in self.events if e["actorId"] == kw["actorId"] and e["sessionId"] == kw["sessionId"]]
        return {"events": [{"eventTimestamp": e["eventTimestamp"], "payload": e["payload"]} for e in evs]}

    def retrieve_memory_records(self, **kw):
        self._maybe_fail("retrieve_memory_records")
        return {"memoryRecordSummaries": self.records.get(kw["namespace"], [])}

    def list_memory_records(self, **kw):
        self._maybe_fail("list_memory_records")
        return {"memoryRecordSummaries": self.records.get(kw["namespace"], [])}


def rec(text, created):
    return {"memoryRecordId": f"mr-{text[:5]}", "content": {"text": text}, "createdAt": created,
            "namespaces": ["x"], "memoryStrategyId": "user_preferences-1", "score": 0.5}


def gw(**kw):
    return MemoryGateway(memory_id="mem-123", region="ap-south-1", client=FakeClient(**kw))


def test_actor_id_validation():
    assert validate_actor_id(" USER-A ") == "USER-A"
    for bad in [None, "", "a b", "../x", "x" * 101, 42]:
        with pytest.raises(ValueError):
            validate_actor_id(bad)


def test_namespace_is_actor_scoped():
    assert preference_namespace("USER-A") == "/actor/USER-A/preferences/"
    g = gw(records={"/actor/USER-A/preferences/": [rec("prefers bullets", T0)]})
    assert g.retrieve_preferences("USER-A", "format").status == "ok"
    other = g.retrieve_preferences("USER-B", "format")  # USER-A's record must never appear
    assert other.status == "empty" and other.data == []


def test_retrieval_newest_first():
    ns = "/actor/USER-A/preferences/"
    g = gw(records={ns: [rec("prefers bullets", T0), rec("prefers tables", T0 + timedelta(days=1))]})
    res = g.retrieve_preferences("USER-A", "format")
    assert [r["text"] for r in res.data] == ["prefers tables", "prefers bullets"]


def test_retrieval_failure_is_unavailable_not_empty():
    res = gw(fail={"retrieve_memory_records"}).retrieve_preferences("USER-A", "format")
    assert res.status == "unavailable" and "AccessDenied" in res.error
    sim = gw().retrieve_preferences("USER-A", "format", simulate_failure=True)
    assert sim.status == "unavailable" and "simulated" in sim.error


def test_save_and_reload_history_in_order():
    g = gw()
    for i, (role, text) in enumerate([("USER", "hi"), ("ASSISTANT", "hello"), ("USER", "create X")]):
        g.client.create_event(memoryId="m", actorId="USER-A", sessionId="s1",
                              eventTimestamp=T0 + timedelta(seconds=i),
                              payload=[{"conversational": {"content": {"text": text}, "role": role}}])
    hist = g.load_history("USER-A", "s1")
    assert hist.status == "ok"
    assert [(r, t) for r, t, _ in hist.data] == [("USER", "hi"), ("ASSISTANT", "hello"), ("USER", "create X")]
    assert g.load_history("USER-B", "s1").status == "empty"  # events are actor-scoped


def test_write_failures_reported():
    res = gw(fail={"create_event"}).save_message("USER-A", "s1", "USER", "remember this")
    assert res.status == "unavailable" and not res.ok
    assert gw().save_message("USER-A", "s1", "USER", "x", simulate_failure=True).status == "unavailable"
    assert gw().save_message("USER-A", "s1", "USER", "x").status == "ok"


def test_disabled_without_memory_id():
    g = MemoryGateway(memory_id="", client=FakeClient())
    assert g.retrieve_preferences("USER-A", "q").status == "disabled"
    assert g.save_message("USER-A", "s", "USER", "x").status == "disabled"


def test_write_honesty_guard():
    from memory import WRITE_FAILED_NOTICE, enforce_write_honesty
    claim = "I've noted your preference for morning planning. This will be available in future sessions."
    out, mod = enforce_write_honesty("Remember that I plan in the morning", claim, user_event_saved=False)
    assert mod and "noted" not in out.lower() and out.endswith(WRITE_FAILED_NOTICE)
    same, mod2 = enforce_write_honesty("Remember X", claim, user_event_saved=True)
    assert same == claim and not mod2
    plain, mod3 = enforce_write_honesty("Show my tasks", "You have no pending tasks.", user_event_saved=False)
    assert plain == "You have no pending tasks." and not mod3


def test_retrieval_honesty_guard():
    from memory import RETRIEVAL_FAILED_NOTICE, enforce_retrieval_honesty
    reply = "I currently do not have any stored preferences about your format. Tell me one."
    out, mod = enforce_retrieval_honesty(reply, "unavailable")
    assert mod and "do not have any stored" not in out and out.endswith(RETRIEVAL_FAILED_NOTICE)
    assert enforce_retrieval_honesty(reply, "empty") == (reply, False)  # empty is a TRUE "no preferences"


def test_retrieval_guard_keeps_task_output():
    from memory import RETRIEVAL_FAILED_NOTICE, enforce_retrieval_honesty
    reply = ("However, I can remember this preference. I have noted your preference. "
             "Your pending tasks: TASK-001 Review budget.")
    out, _ = enforce_retrieval_honesty(reply, "unavailable")
    assert out == "Your pending tasks: TASK-001 Review budget. " + RETRIEVAL_FAILED_NOTICE


def test_retrieval_guard_drops_memory_processing_claims():
    from memory import RETRIEVAL_FAILED_NOTICE, enforce_retrieval_honesty
    reply = "Tell me a format. It will be available in future sessions once memory processing completes."
    out, _ = enforce_retrieval_honesty(reply, "unavailable")
    assert out == "Tell me a format. " + RETRIEVAL_FAILED_NOTICE
