"""Google ADK agent definition and a memory-aware, session-aware runner (V2).

The agent is a single ADK ``LlmAgent`` backed by Amazon Nova (on Amazon
Bedrock) through ADK's LiteLLM model adapter. The model decides which tools to
call; nothing in this module hardcodes an operation sequence.

V2 adds AgentCore Memory context (see ``memory.py``):
* ``memory_context`` (session state, set per turn): retrieved long-term
  preferences plus memory status, rendered into the instruction.
* ``run_turn(history=...)``: when this process has no ADK session for
  (actor, session), the earlier turns loaded from AgentCore Memory events are
  replayed into a fresh ADK session before the new message runs.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import timedelta
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.events import Event
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from task_store import agent_timezone, local_today
from tools import ALL_TOOLS, handle_tool_error

logger = logging.getLogger("productivity_agent")

APP_NAME = "productivity_assistant"
AGENT_NAME = "productivity_assistant"
MODEL_ID = os.environ.get("MODEL_ID", "apac.amazon.nova-lite-v1:0")
AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "1024"))

BASE_INSTRUCTION = """\
You are a Personal Productivity Assistant that manages the user's tasks and
remembers the user's durable preferences.

Tools:
- create_task(title, due_date, user_due_date_phrase): create a task.
  due_date MUST be YYYY-MM-DD; user_due_date_phrase is the date words the user typed.
- list_tasks(status): list tasks; status is "pending", "completed" or "all".
- complete_task(task_id): mark a task as completed, e.g. "TASK-001".

How to work:
1. Work out EVERY operation the user is asking for and call one tool per
   operation, in order. Requests joined by "and", "then" or "also" need
   several tool calls in the same turn. Example: "create X and show my tasks"
   = call create_task, then call list_tasks.
2. Whenever you show tasks to the user, you MUST call list_tasks in this turn
   and show exactly what it returned. Never build a task list from memory,
   from earlier messages or from remembered preferences - tasks may have changed.
3. Convert relative dates ("Friday", "tomorrow", "next Monday", "end of week")
   to an absolute YYYY-MM-DD date using the calendar below. "By <weekday>"
   means the next occurrence of that weekday (today counts if it is that day).
4. The task title is simply what the user wants to do: "Create a task to send
   the weekly report by Friday" -> title "Send the weekly report", due Friday.
   Only if there is no due date (or nothing to do) at all, do NOT call
   create_task: ask for the missing detail. Never invent or assume a due date.
5. Answer the CURRENT message. Use the conversation history to understand it
   ("that task", "it", "what did I ask"), but do not repeat an earlier
   unanswered question unless the current message is about it.
6. Base every statement about tasks ONLY on tool results. Never invent task
   IDs, titles, dates or statuses.
7. If a tool returns "status": "error", tell the user plainly that the
   operation failed and why. Never say an operation succeeded unless its tool
   returned "status": "success". If one step of a multi-step request fails,
   report which steps succeeded and which failed.
8. Keep replies concise, and ALWAYS end your turn with a visible reply to the
   user (not only internal thinking).

Memory rules (long-term preferences are in the MEMORY section below):
M1. Remembered preferences shape HOW you answer (format, tone, planning style).
    They are never a source of tasks or task status - always use list_tasks.
M2. Use a remembered preference only when it is relevant to the request.
M3. An instruction in the current message, or a preference the user stated
    earlier in THIS conversation, overrides remembered preferences.
M4. If remembered preferences conflict, follow the most recent one (listed
    first, with its date) and say briefly which one you followed.
M5. Only claim to remember what is listed in the MEMORY section or said in this
    conversation. If the section says there are no stored preferences, say you
    have no stored preference for this user. If memory is UNAVAILABLE, say you
    cannot access stored preferences right now - never guess or invent one.
M6. When the user states a preference or asks you to remember something,
    acknowledge that preference directly. If "This message was saved to memory:
    YES", say it is noted and will be available in future sessions once memory
    processing completes (it is not instant). If it says NO, you MUST say you
    could not save it to long-term memory - never say it is noted or saved.
M7. Never mention, reveal or guess other users' preferences.
"""

NO_MEMORY_CONTEXT = "MEMORY: not available for this turn. Do not claim to remember any preference."


def build_instruction(ctx: ReadonlyContext) -> str:
    """Dynamic instruction: date calendar + this turn's memory context."""
    today = local_today()
    calendar = "\n".join(
        f"- {(today + timedelta(days=i)).strftime('%A')}: {(today + timedelta(days=i)).isoformat()}"
        + (" (today)" if i == 0 else "")
        for i in range(8)
    )
    memory_context = ctx.state.get("memory_context") or NO_MEMORY_CONTEXT
    return (
        f"{BASE_INSTRUCTION}\n"
        f"Today is {today.strftime('%A')}, {today.isoformat()} (timezone {agent_timezone().key}).\n"
        f"Calendar for the next days:\n{calendar}\n\n"
        f"{memory_context}\n"
    )


def render_memory_context(actor_id: str, saved: str, retrieval_status: str, records: list[dict]) -> str:
    """Render retrieved long-term memory + memory status for the instruction."""
    lines = [f"MEMORY (long-term preferences for the current user, actor '{actor_id}'):"]
    lines.append(f"- This message was saved to memory: {saved}")
    if retrieval_status == "unavailable":
        lines.append("- Long-term memory status: UNAVAILABLE (retrieval failed). You cannot see stored preferences this turn.")
    elif retrieval_status == "disabled":
        lines.append("- Long-term memory status: NOT CONFIGURED. No stored preferences are available.")
    elif not records:
        lines.append("- Long-term memory status: OK, but there are NO stored preferences for this user.")
    else:
        lines.append("- Long-term memory status: OK. Stored preferences, newest first:")
        for r in records:
            date = (r.get("created_at") or "")[:10] or "unknown date"
            lines.append(f"  * [{date}] {r.get('text', '').strip()}")
    return "\n".join(lines)


def build_agent() -> LlmAgent:
    model = LiteLlm(
        model=f"bedrock/converse/{MODEL_ID}",
        aws_region_name=AWS_REGION,
        max_tokens=MAX_TOKENS,
        temperature=0.0,
    )
    return LlmAgent(
        name=AGENT_NAME,
        description="Manages the user's tasks and applies remembered user preferences.",
        model=model,
        instruction=build_instruction,
        tools=ALL_TOOLS,
        on_tool_error_callback=handle_tool_error,
    )


session_service = InMemorySessionService()
root_agent = build_agent()
runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)


async def has_local_session(actor_id: str, session_id: str) -> bool:
    return (
        await session_service.get_session(app_name=APP_NAME, user_id=actor_id, session_id=session_id)
    ) is not None


async def drop_local_session(actor_id: str, session_id: str) -> None:
    """Forget the in-process ADK session (used to simulate a process restart)."""
    if await has_local_session(actor_id, session_id):
        await session_service.delete_session(app_name=APP_NAME, user_id=actor_id, session_id=session_id)


async def ensure_session(actor_id: str, session_id: str, history: list[tuple[str, str, Any]] | None) -> int:
    """Create the ADK session if missing, replaying earlier turns from AgentCore Memory.

    Returns the number of messages restored (0 if the session already existed).
    """
    if await has_local_session(actor_id, session_id):
        return 0
    session = await session_service.create_session(app_name=APP_NAME, user_id=actor_id, session_id=session_id)
    restored = 0
    for i, (role, text, _ts) in enumerate(history or []):
        is_user = role == "USER"
        event = Event(
            invocation_id=f"restored-{i}",
            author="user" if is_user else AGENT_NAME,
            content=types.Content(role="user" if is_user else "model", parts=[types.Part(text=text)]),
        )
        await session_service.append_event(session, event)
        restored += 1
    if restored:
        logger.info("restored %d messages from AgentCore Memory into ADK session %s", restored, session_id)
    return restored


_THINKING = re.compile(r"<thinking>(.*?)(?:</thinking>|$)", re.DOTALL | re.IGNORECASE)


_WRAPPER_TAGS = re.compile(r"</?(reply|response|answer|output)>", re.IGNORECASE)
_CONTEXT_ECHO = re.compile(
    r"[^.\n]*(saved to memory: (yes|no)|long-term memory status|MEMORY \(long-term)[^.\n]*[.\n]?", re.IGNORECASE
)
_TASK_ID = re.compile(r"\bTASK-\d{3,}\b", re.IGNORECASE)


def split_inline_thinking(text: str) -> tuple[str, list[str]]:
    """Nova sometimes returns <thinking>...</thinking> inline in the answer text; split it out.

    Also unwraps <reply>-style wrapper tags and removes sentences that echo the
    internal MEMORY context block.
    """
    thoughts = [t.strip() for t in _THINKING.findall(text) if t.strip()]
    visible = _THINKING.sub("", text)
    visible = _WRAPPER_TAGS.sub("", visible)
    visible = _CONTEXT_ECHO.sub("", visible)
    return visible.strip(), thoughts


def ungrounded_task_ids(response: str, tool_calls: list[dict[str, Any]]) -> set[str]:
    """Task ids mentioned in the reply that no tool call in this turn returned (MEM-10 check)."""
    mentioned = {m.upper() for m in _TASK_ID.findall(response or "")}
    if not mentioned:
        return set()
    returned = {m.upper() for c in tool_calls for m in _TASK_ID.findall(json.dumps(c.get("result") or {}))}
    return mentioned - returned


EMPTY_REPLY_NUDGE = (
    "(Your previous turn ended without a visible reply. Reply to my previous message now, "
    "following all the rules.)"
)


async def run_turn(prompt: str, session_id: str, actor_id: str, memory_context: str) -> dict[str, Any]:
    """Run one user turn through the ADK agent loop and collect the outcome.

    The ADK session must exist (see ``ensure_session``). Returns the final
    answer plus every tool call/response, which makes the agent's orchestration
    decisions visible in the API response and logs.

    Nova Lite occasionally ends a turn with only internal <thinking> text. In
    that case the model is asked once more for a visible reply, and the result
    is flagged with ``retried_empty_reply``.
    """
    result = await _run_once(prompt, session_id, actor_id, memory_context)
    result["retried_empty_reply"] = False
    result["grounding_retry"] = False
    if not result["response"]:
        logger.warning("empty reply from model (thought-only); retrying once session=%s", session_id)
        _merge(result, await _run_once(EMPTY_REPLY_NUDGE, session_id, actor_id, memory_context))
        result["retried_empty_reply"] = True

    # Grounding guard (MEM-10): task data shown to the user must come from a tool call in THIS turn.
    stale = ungrounded_task_ids(result["response"], result["tool_calls"])
    if stale:
        logger.warning("reply mentions tasks %s not returned by a tool this turn; grounding retry session=%s",
                       sorted(stale), session_id)
        _merge(result, await _run_once(GROUNDING_NUDGE, session_id, actor_id, memory_context))
        result["grounding_retry"] = True
        result["ungrounded_after_retry"] = sorted(ungrounded_task_ids(result["response"], result["tool_calls"]))
    return result


GROUNDING_NUDGE = (
    "(Check: your previous reply showed task details without calling list_tasks in this turn. "
    "Call list_tasks now and answer my previous message again using only its result.)"
)


def _merge(result: dict[str, Any], retry: dict[str, Any]) -> None:
    result["response"] = retry["response"] or result["response"]
    result["tool_calls"] += retry["tool_calls"]
    result["reasoning"] += retry["reasoning"]


async def _run_once(prompt: str, session_id: str, actor_id: str, memory_context: str) -> dict[str, Any]:
    message = types.Content(role="user", parts=[types.Part(text=prompt)])
    tool_calls: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    final_text: list[str] = []
    reasoning: list[str] = []

    async for event in runner.run_async(
        user_id=actor_id,
        session_id=session_id,
        new_message=message,
        state_delta={"memory_context": memory_context},
    ):
        for call in event.get_function_calls() or []:
            entry = {"tool": call.name, "args": dict(call.args or {}), "result": None}
            tool_calls.append(entry)
            pending[call.id or f"{call.name}-{len(tool_calls)}"] = entry
        for resp in event.get_function_responses() or []:
            entry = pending.pop(resp.id, None) if resp.id else None
            if entry is None:  # fall back to the latest unmatched call of that tool
                entry = next(
                    (c for c in reversed(tool_calls) if c["tool"] == resp.name and c["result"] is None),
                    None,
                )
            if entry is not None:
                entry["result"] = resp.response
        for part in (event.content.parts if event.content else None) or []:
            text = (getattr(part, "text", None) or "").strip()
            if not text:
                continue
            if getattr(part, "thought", False):
                reasoning.append(text)  # model's reasoning: kept out of the user answer
            elif event.is_final_response():
                visible, inline_thoughts = split_inline_thinking(text)
                reasoning.extend(inline_thoughts)
                if visible:
                    final_text.append(visible)

    for call in tool_calls:
        logger.info("tool_call %s args=%s result=%s", call["tool"], call["args"], call["result"])

    return {
        "response": "\n".join(final_text).strip(),
        "tool_calls": tool_calls,
        "reasoning": reasoning,
    }
