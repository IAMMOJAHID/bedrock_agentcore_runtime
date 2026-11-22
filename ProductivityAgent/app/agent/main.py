"""AgentCore Runtime entrypoint for the memory-enabled Personal Productivity Assistant (V2).

Request payload:
    {
      "actor_id": "USER-A",                        # REQUIRED - who the user is (no default identity)
      "prompt": "Show my tasks in my preferred format",
      "simulate_failure": ["memory_retrieve"],     # optional: list_tasks | create_task | complete_task
                                                   #   (sticky for the session; [] clears)
                                                   #   memory_retrieve | memory_write (this request only)
      "simulate_restart": true                     # optional: drop this process's local state for the
                                                   #   actor/session first (demo of process re-creation)
    }

Identity: ``actor_id`` scopes long-term memory, events and tasks. The session id
comes from the AgentCore runtime session header (``agentcore invoke
--session-id``). In this POC the caller asserts ``actor_id``; in production it
must come from a verified identity (e.g. JWT ``sub`` via a runtime authorizer).

Per turn:
  1. If this process has no ADK session for (actor, session): load earlier
     turns from AgentCore Memory (ListEvents) and replay them   -> short-term memory
  2. Save the user message as an event (CreateEvent)            -> feeds long-term extraction
  3. Retrieve the actor's preferences (RetrieveMemoryRecords)   -> long-term memory
  4. Run the ADK agent with the memory context
  5. Save the assistant reply as an event (CreateEvent)
"""

from __future__ import annotations

import logging
import os
import uuid

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agent import MODEL_ID, drop_local_session, ensure_session, has_local_session, render_memory_context, run_turn
from memory import STATUS_OK, enforce_retrieval_honesty, enforce_write_honesty, gateway, validate_actor_id
from tools import TOOL_NAMES, set_simulated_failures, reset_actor_store

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logger = logging.getLogger("productivity_agent.main")

app = BedrockAgentCoreApp()

LOCAL_SESSION_ID = "local-dev-session-0000000000000000"
MEMORY_FAILURES = ("memory_retrieve", "memory_write")


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
        return {"error": "Missing 'prompt'.", "session_id": session_id}
    try:
        actor_id = validate_actor_id(payload.get("actor_id"))
    except ValueError as exc:
        return {"error": str(exc), "session_id": session_id}
    prompt = prompt.strip()

    # ---- failure injection (demo scenarios 6/7) ----------------------------
    requested = payload.get("simulate_failure")
    requested = [requested] if isinstance(requested, str) else (requested or [])
    unknown = [t for t in requested if t not in TOOL_NAMES + MEMORY_FAILURES]
    if unknown:
        return {"error": f"Unknown simulate_failure value(s): {unknown}. Valid: {list(TOOL_NAMES + MEMORY_FAILURES)}"}
    if "simulate_failure" in payload:
        set_simulated_failures(session_id, [t for t in requested if t in TOOL_NAMES])
    fail_retrieve = "memory_retrieve" in requested
    fail_write = "memory_write" in requested

    if payload.get("simulate_restart"):
        await drop_local_session(actor_id, session_id)
        reset_actor_store(actor_id)
        logger.warning("simulated process restart for actor=%s session=%s", actor_id, session_id)

    logger.info("invoke actor=%s session=%s model=%s memory=%s prompt=%r",
                actor_id, session_id, MODEL_ID, gateway.memory_id or "-", prompt)
    mem: dict = {"memory_id": gateway.memory_id or None, "actor_id": actor_id, "session_id": session_id}
    try:
        # 1. short-term memory: restore this session's history if this process doesn't have it
        history_status, history = "local", []
        if not await has_local_session(actor_id, session_id):
            loaded = gateway.load_history(actor_id, session_id)
            history_status, history = loaded.status, loaded.data or []
            if loaded.error:
                mem["history_error"] = loaded.error
        mem["history_source"] = history_status
        mem["history_restored"] = await ensure_session(actor_id, session_id, history)

        # 2. persist the user's message (this is what long-term extraction reads)
        saved_user = gateway.save_message(actor_id, session_id, "USER", prompt, simulate_failure=fail_write)
        mem["user_event"] = {"status": saved_user.status, "event_id": saved_user.data, "error": saved_user.error}

        # 3. long-term memory: retrieve relevant preferences for THIS actor only
        prefs = gateway.retrieve_preferences(actor_id, prompt, simulate_failure=fail_retrieve)
        mem["retrieval"] = {
            "status": prefs.status,
            "namespace": prefs.details.get("namespace"),
            "records": prefs.data,
            "error": prefs.error,
        }
        memory_context = render_memory_context(
            actor_id,
            saved="YES" if saved_user.status == STATUS_OK else "NO",
            retrieval_status=prefs.status,
            records=prefs.data or [],
        )

        # 4. run the agent
        result = await run_turn(prompt, session_id=session_id, actor_id=actor_id, memory_context=memory_context)

        # 4b. honesty guard: never let the reply claim a failed memory write succeeded (MEM-09)
        result["response"], guarded = enforce_write_honesty(
            prompt, result["response"], user_event_saved=saved_user.status == STATUS_OK
        )
        mem["write_honesty_guard_applied"] = guarded
        result["response"], read_guarded = enforce_retrieval_honesty(result["response"], prefs.status)
        mem["retrieval_honesty_guard_applied"] = read_guarded
        if guarded or read_guarded:
            logger.warning("memory honesty guard applied (write=%s read=%s) actor=%s session=%s",
                           guarded, read_guarded, actor_id, session_id)

        # 5. persist the assistant reply
        if result["response"]:
            saved_reply = gateway.save_message(actor_id, session_id, "ASSISTANT", result["response"],
                                               simulate_failure=fail_write)
            mem["assistant_event"] = {"status": saved_reply.status, "event_id": saved_reply.data,
                                      "error": saved_reply.error}
        return {**result, "memory": mem, "actor_id": actor_id, "session_id": session_id, "model": MODEL_ID}
    except Exception as exc:  # last-resort guard: never crash the runtime on a bad turn
        logger.exception("Agent turn failed for actor %s session %s", actor_id, session_id)
        return {
            "response": (
                "Sorry, I couldn't complete that request because of an internal error. "
                "I can't confirm which operations finished; ask me to list your tasks to check."
            ),
            "error": f"{type(exc).__name__}: {exc}",
            "memory": mem,
            "actor_id": actor_id,
            "session_id": session_id,
        }


if __name__ == "__main__":
    app.run()
