"""V2 demo: AgentCore Memory scenarios, run through the AgentCore CLI, with evidence.

Usage (from ProductivityAgent/):
    python scripts/run_demo_v2.py --mode local     # against `agentcore dev` (port 8080)
    python scripts/run_demo_v2.py --mode remote    # against the deployed AgentCore Runtime

Each run uses fresh actor ids (USER-A-<run>, USER-B-<run>) so earlier runs'
long-term memories can't leak into the results. Long-term extraction is
asynchronous: the script POLLS `agentcore memory record list` until the expected
preference record exists (or times out) instead of assuming it is immediate.

"Process re-creation" (short-term memory continuity):
  local  -> payload {"simulate_restart": true} drops the process's in-memory state
  remote -> `aws bedrock-agentcore stop-runtime-session` terminates the session's
            microVM; the next call with the same session id starts a new one.

Outputs evidence/v2/demo-<mode>.md, demo-<mode>.json and memory-inspection-<mode>.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGION = "ap-south-1"


# --------------------------------------------------------------------------- CLI helpers
def agentcore_cmd() -> list[str]:
    if os.name == "nt":  # call the Node entrypoint directly: the .cmd shim mangles JSON quoting
        js = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@aws/agentcore/dist/index.js"
        node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
        if js.exists():
            return [node, str(js)]
    exe = shutil.which("agentcore")
    if not exe:
        sys.exit("agentcore CLI not found. Install with: npm install -g @aws/agentcore")
    return [exe]


def run(cmd: list[str], timeout: int = 300) -> str:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=env)
    return (p.stdout or "") + (p.stderr or "")


def cli_json(args: list[str]) -> dict:
    out = run(agentcore_cmd() + args + ["--json", "--region", REGION])
    try:
        return json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError):
        return {"_error": out.strip()[:500]}


def project_resources() -> tuple[str, str]:
    status = cli_json(["status"])
    arns = {r["resourceType"]: r.get("arn", "") for r in status.get("resources", [])}
    return arns.get("runtime", ""), arns.get("memory", "").rsplit("/", 1)[-1]


def invoke(mode: str, session_id: str, payload: dict) -> dict:
    cmd = agentcore_cmd() + ["invoke", "--runtime", "agent", "--session-id", session_id,
                             "--payload", json.dumps(payload), "--region", REGION]
    if mode == "local":
        cmd.append("--local")
    raw = run(cmd)
    body = raw.split("status=")[0].strip() if "status=" in raw else raw.strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"response": "", "tool_calls": [], "error": raw.strip()[:1000]}


def restart_session(mode: str, session_id: str, runtime_arn: str) -> str:
    if mode == "local":
        return "local: simulate_restart flag sent with the next request"
    out = run(["aws", "bedrock-agentcore", "stop-runtime-session", "--agent-runtime-arn", runtime_arn,
               "--runtime-session-id", session_id, "--region", REGION, "--output", "json"])
    return "remote: stop-runtime-session -> " + out.strip().replace("\n", " ")[:300]


def list_records(memory_id: str, actor: str) -> list[dict]:
    res = cli_json(["memory", "record", "list", "--id", memory_id, "--namespace", f"/actor/{actor}/preferences/"])
    return res.get("memoryRecordSummaries", [])


def wait_for_record(memory_id: str, actor: str, must_contain: str, timeout: int = 420) -> tuple[list[dict], int]:
    """Poll until a record in the actor's namespace mentions `must_contain` (extraction is async)."""
    start = time.time()
    while True:
        recs = list_records(memory_id, actor)
        if any(must_contain in r.get("content", {}).get("text", "").lower() for r in recs):
            return recs, int(time.time() - start)
        if time.time() - start > timeout:
            return recs, -1
        time.sleep(15)


def list_events(memory_id: str, actor: str, session_id: str) -> list[dict]:
    res = cli_json(["memory", "event", "list", "--id", memory_id, "--actor-id", actor,
                    "--session-id", session_id, "--include-payloads", "--max-results", "100"])
    return res.get("events", [])


# --------------------------------------------------------------------------- checks
def called(res, tool, status=None):
    return [c for c in res.get("tool_calls", []) or []
            if c["tool"] == tool and (status is None or (c.get("result") or {}).get("status") == status)]


def text(res) -> str:
    return (res.get("response") or "").lower()


def has_bullets(res) -> bool:
    return any(re.match(r"^\s*([-*\u2022]|\d+\.)\s+\S", ln) for ln in (res.get("response") or "").splitlines())


def has_table(res) -> bool:
    return sum(1 for ln in (res.get("response") or "").splitlines() if ln.count("|") >= 2) >= 2


def mem(res, *path, default=None):
    cur = res.get("memory") or {}
    for p in path:
        cur = cur.get(p) if isinstance(cur, dict) else None
    return default if cur is None else cur


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["local", "remote"], default="local")
    ap.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%m%d%H%M"))
    args = ap.parse_args()
    mode, run_id = args.mode, args.run_id
    A, B = f"USER-A-{run_id}", f"USER-B-{run_id}"
    S = {k: f"v2-{mode}-{k}-{uuid.uuid4()}" for k in "ABCDE"}
    runtime_arn, memory_id = project_resources()
    print(f"runtime={runtime_arn}\nmemory={memory_id}\nactors={A},{B}")

    results, md = [], [
        f"# V2 memory demo ({mode})", "",
        f"- Started: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Memory: `{memory_id}`  Runtime: `{runtime_arn}`",
        f"- Actors: `{A}`, `{B}`",
        f"- Sessions: " + ", ".join(f"{k}=`{v}`" for k, v in S.items()), "",
    ]
    ok_all = True

    def turn(sid_key, actor, prompt, title, check, **extra):
        nonlocal ok_all
        payload = {"actor_id": actor, "prompt": prompt, **extra}
        res = invoke(mode, S[sid_key], payload)
        passed, note = check(res)
        # global checks on every turn: task ids shown must come from this turn's tool results (MEM-10),
        # and no internal tags/context may leak into the reply
        ids = set(re.findall(r"TASK-\d{3,}", res.get("response") or ""))
        returned = set(re.findall(r"TASK-\d{3,}", json.dumps([c.get("result") for c in res.get("tool_calls") or []])))
        if ids - returned:
            passed, note = False, f"{note} | UNGROUNDED task ids {sorted(ids - returned)}"
        if re.search(r"</?(thinking|reply)>|saved to memory: (yes|no)", res.get("response") or "", re.I):
            passed, note = False, f"{note} | internal tags/context leaked"
        ok_all &= passed
        print(f"[{'PASS' if passed else 'FAIL'}] {title}: {note}")
        results.append({"title": title, "session": S[sid_key], "payload": payload, "passed": passed, "check": note, "result": res})
        md.extend([
            f"## {title} - {'PASS' if passed else 'FAIL'}", "",
            f"Session `{sid_key}` | actor `{actor}` | request: `{json.dumps({k: v for k, v in payload.items() if k != 'actor_id'})}`", "",
            "**Tool calls:** " + (", ".join(f"`{c['tool']}({json.dumps(c['args'])})` -> {(c.get('result') or {}).get('status')}"
                                            for c in res.get("tool_calls", []) or []) or "(none)"), "",
            f"**Memory:** history_source=`{mem(res, 'history_source')}` restored=`{mem(res, 'history_restored')}` "
            f"user_event=`{mem(res, 'user_event', 'status')}` retrieval=`{mem(res, 'retrieval', 'status')}` "
            f"records=`{[r.get('text') for r in mem(res, 'retrieval', 'records', default=[])]}`", "",
            "**Agent response:**", "", "> " + (res.get("response") or res.get("error", "")).replace("\n", "\n> "), "",
            f"_Check: {note}_", "",
        ])
        return res

    def note(line):
        print("   " + line)
        md.extend([f"> **Memory check:** {line}", ""])

    # 1. Session continuity --------------------------------------------------
    turn("A", A, "Create a task for architecture review.", "1a Session continuity - request without due date",
         lambda r: (not called(r, "create_task", "success"), "no task created without a due date (asks for it)"))
    turn("A", A, "What task did I just ask you to create?", "1b Session continuity - same session follow-up (MEM-03)",
         lambda r: ("architecture review" in text(r), "recalls 'architecture review' from session context"))
    note(restart_session(mode, S["A"], runtime_arn))
    turn("A", A, "Remind me: what task did I ask you to create earlier in this conversation?",
         "1c Continuity after the agent process is re-created (short-term memory from events)",
         lambda r: ((mem(r, "history_restored", default=0) or 0) > 0 and "architecture review" in text(r),
                    f"history restored from AgentCore Memory events: {mem(r, 'history_restored')} messages"),
         **({"simulate_restart": True} if mode == "local" else {}))

    # 2. Store preference -----------------------------------------------------
    turn("A", A, "Remember that I prefer short bullet-point task summaries.", "2 Store preference (MEM-04)",
         lambda r: (mem(r, "user_event", "status") == "ok" and "bullet" in text(r),
                    f"user event persisted ({mem(r, 'user_event', 'event_id')}) and preference acknowledged"))
    recs, waited = wait_for_record(memory_id, A, "bullet")
    note(f"long-term extraction for {A}: {'found after ~%ds' % waited if waited >= 0 else 'NOT found before timeout'}; "
         f"records={[r['content']['text'] for r in recs]}")
    ok_all &= waited >= 0

    # 3. New-session recall (+ live tasks via tools) -------------------------
    turn("B", A, "Create a task to prepare the demo slides by Friday and a task to review the budget by Monday.",
         "3a New session B - create tasks (tool source of truth)",
         lambda r: (len(called(r, "create_task", "success")) == 2, "two tasks created"))
    turn("B", A, "Show my tasks in my preferred format.", "3b New-session recall - bullet format (MEM-06, MEM-10)",
         lambda r: (mem(r, "retrieval", "status") == "ok" and bool(called(r, "list_tasks", "success"))
                    and has_bullets(r) and not has_table(r),
                    "retrieved preference from long-term memory, listed live tasks via tool, bullet format"))

    # 4. Isolation -----------------------------------------------------------
    turn("C", B, "What output format do I prefer?", "4 Isolation - USER-B (MEM-07)",
         lambda r: (mem(r, "retrieval", "status") == "empty" and "bullet" not in text(r),
                    "USER-B namespace empty; USER-A preference not revealed"))
    note(f"records in USER-B namespace: {len(list_records(memory_id, B))}")

    # 5. Preference update ---------------------------------------------------
    turn("B", A, "From now on, use a table rather than bullet points.", "5a Preference update stated (MEM-08)",
         lambda r: (mem(r, "user_event", "status") == "ok", "update persisted as event"))
    recs, waited = wait_for_record(memory_id, A, "table")
    note(f"updated preference extraction: {'found after ~%ds' % waited if waited >= 0 else 'NOT found before timeout'}; "
         f"records now={[r['content']['text'] for r in recs]}")
    ok_all &= waited >= 0

    # 6. Tool + memory in a new session ---------------------------------------
    turn("D", A, "Create a task to send the weekly report by Friday.", "6a New session D - create task",
         lambda r: (bool(called(r, "create_task", "success")), "task created"))
    turn("D", A, "List my pending tasks using my preferred format.", "6b Tool + updated memory - table format (MEM-08, MEM-10)",
         lambda r: (bool(called(r, "list_tasks", "success")) and has_table(r),
                    "live tasks via list_tasks, rendered as a table (latest preference)"))

    # 7. Memory unavailable --------------------------------------------------
    turn("E", A, "What output format do I prefer?", "7a Memory retrieval failure (MEM-09)",
         lambda r: (mem(r, "retrieval", "status") == "unavailable"
                    and not re.search(r"you prefer|your preferred (output )?format is|prefer(s)? (a )?table",
                                      text(r).replace("tell you what you prefer", ""))  # guard notice wording
                    and any(w in text(r) for w in ("couldn't access", "could not access", "cannot access", "can't access", "unavailable")),
                    f"retrieval unavailable; reply states it and claims no preference "
                    f"(guard applied={mem(r, 'retrieval_honesty_guard_applied')})"),
         simulate_failure=["memory_retrieve"])
    turn("E", A, "Remember that I usually plan my work in the morning.", "7b Memory write failure (MEM-09)",
         lambda r: (mem(r, "user_event", "status") == "unavailable"
                    and any(w in text(r) for w in ("could not", "couldn't", "unable", "not able", "wasn't able", "failed"))
                    and "noted" not in text(r) and "available in future sessions" not in text(r),
                    f"write failed; reply says it could not save, no false claim "
                    f"(guard applied={mem(r, 'write_honesty_guard_applied')})"),
         simulate_failure=["memory_write"])

    # Inspection evidence -----------------------------------------------------
    inspection = {
        "memory_id": memory_id,
        "records": {A: list_records(memory_id, A), B: list_records(memory_id, B)},
        "events": {f"{A}/{S['A']}": list_events(memory_id, A, S["A"]),
                   f"{B}/{S['C']}": list_events(memory_id, B, S["C"])},
    }
    out = ROOT / "evidence" / "v2"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"demo-{mode}.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    (out / f"demo-{mode}.md").write_text("\n".join(md), encoding="utf-8")
    (out / f"memory-inspection-{mode}.json").write_text(json.dumps(inspection, indent=2, default=str), encoding="utf-8")
    print(f"\nEvidence: evidence/v2/demo-{mode}.md, memory-inspection-{mode}.json")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
