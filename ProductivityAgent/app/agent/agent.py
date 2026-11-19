"""Google ADK agent definition and a session-aware runner.

The agent is a single ADK ``LlmAgent`` backed by Amazon Nova (on Amazon
Bedrock) through ADK's LiteLLM model adapter. The model decides which tools to
call; nothing in this module hardcodes an operation sequence.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from task_store import agent_timezone, local_today
from tools import ALL_TOOLS, handle_tool_error

logger = logging.getLogger("productivity_agent")

APP_NAME = "productivity_assistant"
MODEL_ID = os.environ.get("MODEL_ID", "apac.amazon.nova-lite-v1:0")
AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "1024"))

BASE_INSTRUCTION = """\
You are a Personal Productivity Assistant that manages the user's tasks.

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
   and show exactly what it returned. Never build a task list from memory or
   from earlier messages - tasks may have changed.
3. Convert relative dates ("Friday", "tomorrow", "next Monday", "end of week")
   to an absolute YYYY-MM-DD date using the calendar below. "By <weekday>"
   means the next occurrence of that weekday (today counts if it is that day).
4. If the user has not given a task title or a due date, do NOT call
   create_task: ask for the missing detail. Never invent or assume a due date.
5. Use the conversation history: "that task", "the first one" or "it" refer to
   tasks mentioned earlier in this session.
6. Base every statement about tasks ONLY on tool results from this turn or
   earlier turns. Never invent task IDs, titles, dates or statuses.
7. If a tool returns "status": "error", tell the user plainly that the
   operation failed and why (use the message). Never say an operation
   succeeded unless its tool returned "status": "success". If one step of a
   multi-step request fails, report which steps succeeded and which failed.
8. Present task lists clearly: ID, title, due date, status. Keep replies concise.
"""


def build_instruction(_ctx: ReadonlyContext) -> str:
    """Dynamic instruction: inject today's date and a 7-day calendar every turn."""
    today = local_today()
    calendar = "\n".join(
        f"- {(today + timedelta(days=i)).strftime('%A')}: {(today + timedelta(days=i)).isoformat()}"
        + (" (today)" if i == 0 else "")
        for i in range(8)
    )
    return (
        f"{BASE_INSTRUCTION}\n"
        f"Today is {today.strftime('%A')}, {today.isoformat()} (timezone {agent_timezone().key}).\n"
        f"Calendar for the next days:\n{calendar}\n"
    )


def build_agent() -> LlmAgent:
    model = LiteLlm(
        model=f"bedrock/converse/{MODEL_ID}",
        aws_region_name=AWS_REGION,
        max_tokens=MAX_TOKENS,
        temperature=0.0,
    )
    return LlmAgent(
        name="productivity_assistant",
        description="Manages the user's tasks: create, list and complete.",
        model=model,
        instruction=build_instruction,
        tools=ALL_TOOLS,
        on_tool_error_callback=handle_tool_error,
    )


session_service = InMemorySessionService()
root_agent = build_agent()
runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)


async def run_turn(prompt: str, session_id: str, user_id: str = "demo-user") -> dict[str, Any]:
    """Run one user turn through the ADK agent loop and collect the outcome.

    Returns the final answer plus every tool call/response, which makes the
    agent's orchestration decisions visible in the API response and logs.
    """
    session = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if session is None:
        await session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )

    message = types.Content(role="user", parts=[types.Part(text=prompt)])
    tool_calls: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    final_text: list[str] = []
    reasoning: list[str] = []

    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=message):
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
                final_text.append(text)

    for call in tool_calls:
        logger.info("tool_call %s args=%s result=%s", call["tool"], call["args"], call["result"])

    return {
        "response": "\n".join(final_text).strip(),
        "tool_calls": tool_calls,
        "reasoning": reasoning,
        "session_id": session_id,
        "model": MODEL_ID,
    }
