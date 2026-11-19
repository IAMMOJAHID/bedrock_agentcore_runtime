"""AgentCore Runtime entrypoint for the Personal Productivity Assistant.

Request payload:
    {
      "prompt": "Create a task to complete the architecture review by Friday",
      "simulate_failure": ["list_tasks"]   # optional, demo scenario 6; [] turns it off
    }

The conversation is keyed by the AgentCore session id (the
X-Amzn-Bedrock-AgentCore-Runtime-Session-Id header, set by
``agentcore invoke --session-id`` / ``agentcore dev``), so follow-up requests in
the same session share conversation history and tasks.
"""

from __future__ import annotations

import logging
import os
import uuid

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agent import MODEL_ID, run_turn
from tools import TOOL_NAMES, set_simulated_failures

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
# LiteLLM is very chatty at INFO; keep our own logs readable.
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logger = logging.getLogger("productivity_agent.main")

app = BedrockAgentCoreApp()

LOCAL_SESSION_ID = "local-dev-session-0000000000000000"


@app.entrypoint
async def invoke(payload, context):
    payload = payload or {}
    session_id = (
        getattr(context, "session_id", None)
        or payload.get("session_id")
        or (LOCAL_SESSION_ID if os.environ.get("LOCAL_DEV") else str(uuid.uuid4()))
    )
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return {
            "error": "Missing 'prompt'. Send a JSON payload like {\"prompt\": \"Show my pending tasks\"}.",
            "session_id": session_id,
        }

    if "simulate_failure" in payload:
        requested = payload.get("simulate_failure") or []
        if isinstance(requested, str):
            requested = [requested]
        unknown = [t for t in requested if t not in TOOL_NAMES]
        if unknown:
            return {"error": f"Unknown tool(s) in simulate_failure: {unknown}. Valid: {list(TOOL_NAMES)}"}
        set_simulated_failures(session_id, requested)
        logger.warning("Failure injection for session %s: %s", session_id, requested or "disabled")

    logger.info("invoke session=%s model=%s prompt=%r", session_id, MODEL_ID, prompt)
    try:
        return await run_turn(prompt.strip(), session_id=session_id)
    except Exception as exc:  # last-resort guard: never crash the runtime on a bad turn
        logger.exception("Agent turn failed for session %s", session_id)
        return {
            "response": (
                "Sorry, I couldn't complete that request because of an internal error. "
                "I can't confirm which operations finished; ask me to list your tasks to check."
            ),
            "error": f"{type(exc).__name__}: {exc}",
            "session_id": session_id,
        }


if __name__ == "__main__":
    app.run()
