# productivity-agent (CodeZip payload, V2)

Google ADK agent with AgentCore Memory, deployed to Amazon Bedrock AgentCore Runtime. See the
[project README](../../README.md) for setup, memory design, deployment and the demo.

| File | Purpose |
|---|---|
| `main.py` | AgentCore entrypoint: requires `actor_id`; restores history, writes events, retrieves preferences, applies guards |
| `memory.py` | AgentCore Memory gateway (CreateEvent / ListEvents / RetrieveMemoryRecords / ListMemoryRecords) with explicit `ok` / `empty` / `unavailable` / `disabled` status; honesty guards; actor-id validation |
| `agent.py` | ADK `LlmAgent` on Amazon Nova Lite (via `LiteLlm`); memory-aware dynamic instruction; history replay into ADK sessions; grounding and empty-reply retries; output hygiene |
| `tools.py` | `create_task`, `list_tasks`, `complete_task` (task store per actor, in-process only); tool-error callback; failure injection |
| `task_store.py` | In-memory task store with validation |
| `tests/` | Unit tests: `uv run pytest -q` (34) |
