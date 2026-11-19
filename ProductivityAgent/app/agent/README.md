# productivity-agent (CodeZip payload)

Google ADK agent code deployed to Amazon Bedrock AgentCore Runtime. See the
[project README](../../README.md) for setup, deployment and the demo.

| File | Purpose |
|---|---|
| `main.py` | AgentCore entrypoint (`BedrockAgentCoreApp`); maps the runtime session id to an ADK session |
| `agent.py` | ADK `LlmAgent` on Amazon Nova Lite (via `LiteLlm`), dynamic date-aware instruction, runner |
| `tools.py` | `create_task`, `list_tasks`, `complete_task`; tool-error callback; failure injection |
| `task_store.py` | in-memory task store with validation |
| `tests/` | unit tests: `uv run pytest -q` |
