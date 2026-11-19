"""Run the assignment's demo scenarios through the AgentCore CLI and record evidence.

Usage (from the ProductivityAgent/ directory):
    python scripts/run_demo.py --mode local    # against `agentcore dev` (port 8080)
    python scripts/run_demo.py --mode remote   # against the deployed AgentCore Runtime

Every scenario is sent with `agentcore invoke` in ONE session, so later turns can
rely on earlier ones (AC-07). For each turn the script checks which tools the
agent actually called and what they returned (taken from the response's
`tool_calls`), and writes evidence/demo-<mode>.md and evidence/demo-<mode>.json.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (id, title, payload, check) - check(result) -> (passed: bool, note: str)
def called(res, tool, status=None):
    return [
        c for c in res.get("tool_calls", [])
        if c["tool"] == tool and (status is None or (c.get("result") or {}).get("status") == status)
    ]


def not_claiming_success(res):
    text = res.get("response", "").lower()
    return not any(w in text for w in ("successfully created", "has been created", "has been marked"))


SCENARIOS = [
    ("S1", "Create a task (AC-03)",
     {"prompt": "Create a task to complete the architecture review by Friday."},
     lambda r: (bool(called(r, "create_task", "success")), "create_task succeeded")),
    ("S2", "Retrieve pending tasks (AC-04)",
     {"prompt": "Show me all my pending tasks."},
     lambda r: (bool(called(r, "list_tasks", "success")), "list_tasks succeeded")),
    ("S3", "Multi-step interaction (AC-06)",
     {"prompt": "Create a task for reviewing the AgentCore architecture due next Monday and then show me all my pending tasks."},
     lambda r: (bool(called(r, "create_task", "success")) and bool(called(r, "list_tasks", "success")),
                "create_task AND list_tasks in one turn")),
    ("S4", "Complete a task (AC-05)",
     {"prompt": "Mark task TASK-001 as completed."},
     lambda r: (bool(called(r, "complete_task", "success")), "complete_task succeeded")),
    ("S5", "Invalid input - unknown id (AC-08)",
     {"prompt": "Complete task TASK-999."},
     lambda r: (any((c.get("result") or {}).get("error_type") == "not_found" for c in called(r, "complete_task")),
                "complete_task returned not_found")),
    ("S5b", "Invalid input - missing due date (AC-08)",
     {"prompt": "Create a task to call the vendor."},
     lambda r: (not called(r, "create_task", "success"), "no task created without a due date")),
    ("S5c", "Invalid input - impossible date (AC-08)",
     {"prompt": "Create a task to update the roadmap due on 2026-02-30."},
     lambda r: (not called(r, "create_task", "success"), "invalid date rejected")),
    ("S6", "Contextual interaction (AC-07)",
     {"prompt": "Also mark the AgentCore architecture review task I created earlier as completed, then show me everything including completed tasks."},
     lambda r: (any((c.get("args") or {}).get("task_id", "").upper() == "TASK-002" for c in called(r, "complete_task", "success")),
                "resolved 'the AgentCore task' to TASK-002 from session context")),
    ("S7", "Tool failure (AC-09) - list_tasks backend down",
     {"prompt": "Show me all my pending tasks.", "simulate_failure": ["list_tasks"]},
     lambda r: (any((c.get("result") or {}).get("error_type") == "tool_failure" for c in called(r, "list_tasks")),
                "list_tasks failed and agent reported it")),
    ("S7b", "Tool failure (AC-09) - create_task backend down",
     {"prompt": "Create a task to prepare the demo slides by Friday.", "simulate_failure": ["create_task"]},
     lambda r: (any((c.get("result") or {}).get("error_type") == "tool_failure" for c in called(r, "create_task"))
                and not_claiming_success(r), "create_task failed, no false success claim")),
    ("S7c", "Recovery after failure injection is disabled",
     {"prompt": "The backend is back. Show me my pending tasks.", "simulate_failure": []},
     lambda r: (bool(called(r, "list_tasks", "success")), "list_tasks succeeds again")),
]


def agentcore_cmd() -> list[str]:
    """Locate the AgentCore CLI; call its Node entrypoint directly on Windows to avoid
    .cmd shim quoting problems with JSON payloads."""
    if os.name == "nt":
        js = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@aws/agentcore/dist/index.js"
        node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
        if js.exists():
            return [node, str(js)]
    exe = shutil.which("agentcore")
    if not exe:
        sys.exit("agentcore CLI not found. Install with: npm install -g @aws/agentcore")
    return [exe]


def invoke(mode: str, session_id: str, payload: dict) -> tuple[dict, str]:
    cmd = agentcore_cmd() + ["invoke", "--runtime", "agent", "--session-id", session_id,
                             "--payload", json.dumps(payload)]
    if mode == "local":
        cmd.append("--local")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=300)
    raw = (proc.stdout or "") + (proc.stderr or "")
    body = raw.split("status=")[0].strip() if "status=" in raw else raw.strip()
    try:
        return json.loads(body), raw
    except json.JSONDecodeError:
        return {"response": "", "tool_calls": [], "error": raw.strip()}, raw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["local", "remote"], default="local")
    ap.add_argument("--session-id", default=None)
    args = ap.parse_args()
    session_id = args.session_id or f"demo-{args.mode}-{uuid.uuid4()}"

    out_dir = ROOT / "evidence"
    out_dir.mkdir(exist_ok=True)
    results, md = [], [
        f"# Demo run ({args.mode})",
        "",
        f"- Started: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Session ID: `{session_id}`",
        "",
    ]
    passed_all = True
    for sid, title, payload, check in SCENARIOS:
        res, raw = invoke(args.mode, session_id, payload)
        ok, note = check(res) if "error" not in res or res.get("tool_calls") is not None else (False, "invoke failed")
        passed_all &= ok
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {sid} {title}: {note}")
        results.append({"id": sid, "title": title, "payload": payload, "passed": ok, "check": note, "result": res})
        md += [
            f"## {sid}. {title} - {mark}",
            "",
            f"**Request:** `{json.dumps(payload)}`",
            "",
            "**Tool calls:**",
            "",
        ]
        for c in res.get("tool_calls", []) or []:
            md.append(f"- `{c['tool']}({json.dumps(c['args'])})` -> `{json.dumps(c.get('result'))}`")
        if not res.get("tool_calls"):
            md.append("- (none)")
        md += ["", "**Agent response:**", "", "> " + (res.get("response") or res.get("error", "")).replace("\n", "\n> "), "",
               f"_Check: {note}_", ""]

    (out_dir / f"demo-{args.mode}.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (out_dir / f"demo-{args.mode}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\nSession: {session_id}\nEvidence: evidence/demo-{args.mode}.md")
    return 0 if passed_all else 1


if __name__ == "__main__":
    sys.exit(main())
