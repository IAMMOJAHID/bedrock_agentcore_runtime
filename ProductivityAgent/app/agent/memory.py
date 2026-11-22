"""AgentCore Memory integration for the Personal Productivity Assistant (V2).

Two kinds of memory, one AgentCore Memory resource:

* Short-term memory: every user and assistant message is written as a
  conversational event under (actor_id, session_id) with CreateEvent. When the
  agent process starts without local state for a session (new microVM, cold
  start, restart), the recent turns are read back with ListEvents and replayed
  into the ADK session, so the conversation continues.

* Long-term memory: the Memory resource's USER_PREFERENCE strategy
  asynchronously extracts durable preferences from those events into memory
  records under ``/actor/{actorId}/preferences/``. Each turn retrieves the
  relevant records with RetrieveMemoryRecords (semantic search scoped to that
  actor's namespace) and gives them to the model as context.

Every operation returns an explicit status ("ok" / "empty" / "unavailable" /
"disabled") instead of raising, and failures are logged at ERROR. The SDK's
``MemoryClient.retrieve_memories`` turns every ClientError into ``[]``, which
would make an outage look like "no preferences", so the boto3 data-plane API is
used directly here.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config

logger = logging.getLogger("productivity_agent.memory")

# Env var injected by the AgentCore CDK construct for the project memory "TaskMemory".
MEMORY_ID = os.environ.get("MEMORY_TASKMEMORY_ID") or os.environ.get("MEMORY_ID") or ""
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
PREFERENCE_NAMESPACE = "/actor/{actor_id}/preferences/"  # must match agentcore.json namespaceTemplates
HISTORY_TURNS = int(os.environ.get("MEMORY_HISTORY_TURNS", "10"))
RETRIEVE_TOP_K = int(os.environ.get("MEMORY_TOP_K", "5"))
MAX_EVENT_CHARS = 9000  # keep event payloads well inside service limits

# AgentCore actor ids: letters, digits, and _ - . : / @ (we keep it stricter).
ACTOR_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-.@]{0,99}$")

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_UNAVAILABLE = "unavailable"
STATUS_DISABLED = "disabled"


class SimulatedMemoryFailure(RuntimeError):
    """Raised when failure injection is requested for a memory operation (demo scenario 7)."""


@dataclass
class MemoryResult:
    status: str
    data: Any = None
    error: str | None = None
    details: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_OK, STATUS_EMPTY)


def preference_namespace(actor_id: str) -> str:
    return PREFERENCE_NAMESPACE.format(actor_id=actor_id)


def validate_actor_id(actor_id: Any) -> str:
    if not isinstance(actor_id, str) or not ACTOR_ID_PATTERN.match(actor_id.strip()):
        raise ValueError(
            "A valid 'actor_id' is required (1-100 chars: letters, digits, _ - . @), e.g. \"USER-A\"."
        )
    return actor_id.strip()


class MemoryGateway:
    """Thin, failure-aware wrapper over the AgentCore Memory data plane."""

    def __init__(self, memory_id: str = MEMORY_ID, region: str = REGION, client: Any = None) -> None:
        self.memory_id = memory_id
        self.region = region
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(self.memory_id)

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client(
                "bedrock-agentcore",
                region_name=self.region,
                config=Config(retries={"max_attempts": 3, "mode": "adaptive"}, connect_timeout=5, read_timeout=15),
            )
        return self._client

    # ----------------------------------------------------------------- write
    def save_message(
        self, actor_id: str, session_id: str, role: str, text: str, simulate_failure: bool = False
    ) -> MemoryResult:
        """Persist one conversational message (role USER or ASSISTANT) as an event."""
        if not self.enabled:
            return MemoryResult(STATUS_DISABLED, error="Memory not configured (MEMORY_TASKMEMORY_ID unset).")
        text = (text or "").strip()
        if not text:
            return MemoryResult(STATUS_EMPTY)
        try:
            if simulate_failure:
                raise SimulatedMemoryFailure("AgentCore Memory CreateEvent unavailable (simulated failure).")
            resp = self.client.create_event(
                memoryId=self.memory_id,
                actorId=actor_id,
                sessionId=session_id,
                eventTimestamp=datetime.now(timezone.utc),
                payload=[{"conversational": {"content": {"text": text[:MAX_EVENT_CHARS]}, "role": role}}],
            )
            event_id = resp["event"]["eventId"]
            logger.info("memory.create_event ok actor=%s session=%s role=%s event=%s", actor_id, session_id, role, event_id)
            return MemoryResult(STATUS_OK, data=event_id)
        except Exception as exc:  # noqa: BLE001 - every failure must be reported, never raised
            logger.error("memory.create_event FAILED actor=%s session=%s role=%s: %r", actor_id, session_id, role, exc)
            return MemoryResult(STATUS_UNAVAILABLE, error=f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------ short-term
    def load_history(self, actor_id: str, session_id: str, max_turns: int = HISTORY_TURNS) -> MemoryResult:
        """Read this session's earlier messages (oldest first) as [(role, text, timestamp)]."""
        if not self.enabled:
            return MemoryResult(STATUS_DISABLED, data=[])
        try:
            events: list[dict] = []
            token = None
            while True:
                kwargs = dict(memoryId=self.memory_id, actorId=actor_id, sessionId=session_id,
                              includePayloads=True, maxResults=100)
                if token:
                    kwargs["nextToken"] = token
                resp = self.client.list_events(**kwargs)
                events.extend(resp.get("events", []))
                token = resp.get("nextToken")
                if not token or len(events) >= max_turns * 4:
                    break
            messages = []
            for ev in sorted(events, key=lambda e: e["eventTimestamp"]):
                for item in ev.get("payload", []):
                    conv = item.get("conversational")
                    if conv and conv.get("content", {}).get("text"):
                        messages.append((conv["role"], conv["content"]["text"], ev["eventTimestamp"]))
            messages = messages[-max_turns * 2:]
            logger.info("memory.list_events ok actor=%s session=%s messages=%d", actor_id, session_id, len(messages))
            return MemoryResult(STATUS_OK if messages else STATUS_EMPTY, data=messages)
        except Exception as exc:  # noqa: BLE001
            logger.error("memory.list_events FAILED actor=%s session=%s: %r", actor_id, session_id, exc)
            return MemoryResult(STATUS_UNAVAILABLE, data=[], error=f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------- long-term
    def retrieve_preferences(
        self, actor_id: str, query: str, top_k: int = RETRIEVE_TOP_K, simulate_failure: bool = False
    ) -> MemoryResult:
        """Semantic search over this actor's preference namespace only."""
        namespace = preference_namespace(actor_id)
        if not self.enabled:
            return MemoryResult(STATUS_DISABLED, data=[], details={"namespace": namespace})
        try:
            if simulate_failure:
                raise SimulatedMemoryFailure("AgentCore Memory RetrieveMemoryRecords unavailable (simulated failure).")
            resp = self.client.retrieve_memory_records(
                memoryId=self.memory_id,
                namespace=namespace,
                searchCriteria={"searchQuery": (query or "user preferences")[:1000], "topK": top_k},
                maxResults=top_k,
            )
            records = [_record_view(r) for r in resp.get("memoryRecordSummaries", [])]
            # Newest first, so the model sees the latest preference before older ones.
            records.sort(key=lambda r: r.get("created_at") or "", reverse=True)
            logger.info("memory.retrieve ok actor=%s namespace=%s records=%d ids=%s",
                        actor_id, namespace, len(records), [r["id"] for r in records])
            return MemoryResult(STATUS_OK if records else STATUS_EMPTY, data=records, details={"namespace": namespace})
        except Exception as exc:  # noqa: BLE001
            logger.error("memory.retrieve FAILED actor=%s namespace=%s: %r", actor_id, namespace, exc)
            return MemoryResult(STATUS_UNAVAILABLE, data=[], error=f"{type(exc).__name__}: {exc}",
                                details={"namespace": namespace})

    def list_preferences(self, actor_id: str) -> MemoryResult:
        """Every long-term record in the actor's namespace (used for inspection/verification)."""
        namespace = preference_namespace(actor_id)
        if not self.enabled:
            return MemoryResult(STATUS_DISABLED, data=[])
        try:
            records, token = [], None
            while True:
                kwargs = dict(memoryId=self.memory_id, namespace=namespace, maxResults=100)
                if token:
                    kwargs["nextToken"] = token
                resp = self.client.list_memory_records(**kwargs)
                records.extend(_record_view(r) for r in resp.get("memoryRecordSummaries", []))
                token = resp.get("nextToken")
                if not token:
                    break
            records.sort(key=lambda r: r.get("created_at") or "", reverse=True)
            return MemoryResult(STATUS_OK if records else STATUS_EMPTY, data=records, details={"namespace": namespace})
        except Exception as exc:  # noqa: BLE001
            logger.error("memory.list_records FAILED actor=%s: %r", actor_id, exc)
            return MemoryResult(STATUS_UNAVAILABLE, data=[], error=f"{type(exc).__name__}: {exc}")


_SAVE_CLAIM = re.compile(
    r"(\bnoted\b|\bi(?:'ll| will) remember\b|\bi(?:'ve| have) (?:saved|stored|remembered|recorded)\b"
    r"|\b(?:saved|stored) (?:it |this |that )?(?:to|in) (?:your )?(?:long-term )?memory\b"
    r"|\bavailable in future sessions\b|\bwill remember\b)",
    re.IGNORECASE,
)
_MEMORY_REQUEST = re.compile(r"\b(remember|prefer|preference|from now on|always|usually)\b", re.IGNORECASE)
WRITE_FAILED_NOTICE = (
    "Note: I couldn't save this to long-term memory (the memory service is unavailable right now), "
    "so I won't be able to recall it in future sessions. Please tell me again later."
)


def enforce_write_honesty(prompt: str, response: str, user_event_saved: bool) -> tuple[str, bool]:
    """Code-level guarantee for MEM-09: never claim a failed memory write succeeded.

    If saving the user's message failed, drop any sentence that claims it was
    noted/saved/remembered and, for memory-related requests, append an explicit
    notice. Returns (response, modified).
    """
    if user_event_saved:
        return response, False
    sentences = re.split(r"(?<=[.!?])\s+", response.strip()) if response.strip() else []
    kept = [s for s in sentences if not _SAVE_CLAIM.search(s)]
    modified = len(kept) != len(sentences)
    if modified or _MEMORY_REQUEST.search(prompt or ""):
        kept.append(WRITE_FAILED_NOTICE)
        modified = True
    return " ".join(kept).strip(), modified


_NO_PREFS_CLAIM = re.compile(
    r"(\bno (?:stored |saved )?preferences?\b|\b(?:do not|don't) have (?:any )?(?:stored |saved )?preferences?\b"
    r"|\bhaven't (?:told|shared|given)\b|\bno record of\b)",
    re.IGNORECASE,
)
_PREFERENCE_TALK = re.compile(r"(preferen|\bprefer\b|\bnoted\b|\bremember)", re.IGNORECASE)
RETRIEVAL_FAILED_NOTICE = (
    "Note: I couldn't access your stored preferences just now (the memory service is unavailable), "
    "so I can't tell you what you prefer and haven't applied any remembered preferences. Please try again shortly."
)


def enforce_retrieval_honesty(response: str, retrieval_status: str) -> tuple[str, bool]:
    """Code-level guarantee for MEM-09 on reads.

    When retrieval FAILED the agent cannot know the user's preferences, so any
    statement about them ("you have no stored preferences", "I've noted...")
    is unsupported. Drop every sentence that talks about preferences, keep the
    rest (e.g. a task list from tools) and state the failure explicitly.
    """
    if retrieval_status != STATUS_UNAVAILABLE:
        return response, False
    sentences = re.split(r"(?<=[.!?])\s+", response.strip()) if response.strip() else []
    kept = [s for s in sentences
            if not (_NO_PREFS_CLAIM.search(s) or _PREFERENCE_TALK.search(s) or _SAVE_CLAIM.search(s))]
    kept.append(RETRIEVAL_FAILED_NOTICE)
    return " ".join(kept).strip(), True


def _readable(text: str) -> str:
    """USER_PREFERENCE records are JSON ({context, preference, categories}); render them as prose."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return text
    if isinstance(data, dict) and data.get("preference"):
        context = data.get("context")
        return f"{data['preference']}" + (f" (applies to: {context})" if context else "")
    return text


def _record_view(summary: dict) -> dict:
    created = summary.get("createdAt")
    raw = (summary.get("content") or {}).get("text", "")
    return {
        "id": summary.get("memoryRecordId"),
        "text": _readable(raw),
        "raw": raw,
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
        "namespaces": summary.get("namespaces"),
        "strategy_id": summary.get("memoryStrategyId"),
        "score": summary.get("score"),
    }


gateway = MemoryGateway()
