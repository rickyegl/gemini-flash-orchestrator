"""Orchestrate Gemini Flash agents through the local Antigravity session.

No API key: every call goes through the signed-in Antigravity app's `agentapi`.
The script finds the running app's local server (address + CSRF token from
its process command line), so Antigravity just needs to be open.

Commands:
  agy.py status                  is the Antigravity app reachable?
  agy.py run TASKS.json [opts]   fan out tasks, wait, print JSON results
  agy.py run --prompt "..." [--workspace DIR]
  agy.py collect RUN_ID          re-check a run started with --no-wait
  agy.py send CONV_ID "message"  follow up in an existing conversation
  agy.py meta CONV_ID            raw conversation metadata

TASKS.json: a list of tasks, or {"defaults": {...}, "tasks": [...]}. Task keys:
  id, prompt (required), workspace, model (flash_lite|flash|pro),
  title, profile, project, timeout (seconds)
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import time
import uuid

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(SKILL_DIR, "state", "runs")
# Antigravity project the workers run under. "outside-of-project" always
# exists; override with --project or AGY_PROJECT_ID.
DEFAULT_PROJECT = os.environ.get("AGY_PROJECT_ID", "outside-of-project")
DONE_MARKER = "<<AGY_DONE>>"
_PROBE_ID = "00000000-0000-0000-0000-000000000000"

RESULT_INSTRUCTIONS = """

---
ORCHESTRATION PROTOCOL (required):
You are a worker agent dispatched by an orchestrator that cannot see this chat.
{workspace_line}When you are completely finished, write your final answer as
Markdown to this exact file (create parent folders if needed):
  {result_path}
The file must be self-contained (findings, changed files, open questions) and
its LAST line must be exactly: {marker}
If you cannot finish, still write the file explaining what blocked you, ending
with the same marker line. Do not ask the user questions; decide and proceed.
"""


def _die(msg: str, code: int = 2):
  print(json.dumps({"error": msg}), file=sys.stderr)
  sys.exit(code)


# --- session discovery / agentapi --------------------------------------------

_PS_LIST = r"""
$procs = Get-CimInstance Win32_Process -Filter "Name='language_server.exe'"
@($procs | ForEach-Object {
  [pscustomobject]@{
    pid = $_.ProcessId; exe = $_.ExecutablePath; cmd = $_.CommandLine
    ports = @(Get-NetTCPConnection -OwningProcess $_.ProcessId -State Listen `
              -ErrorAction SilentlyContinue | ForEach-Object { $_.LocalPort })
  }
}) | ConvertTo-Json -Compress -Depth 3
"""


def _list_servers() -> list[dict]:
  """Running Antigravity language servers with their listening ports."""
  if os.name == "nt":
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", _PS_LIST],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    data = json.loads(out) if out else []
    return [data] if isinstance(data, dict) else data
  servers = []
  ps = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True,
                      text=True).stdout
  for line in ps.splitlines():
    pid, _, cmd = line.strip().partition(" ")
    if "language_server" not in cmd or "--csrf_token" not in cmd:
      continue
    lsof = subprocess.run(
        ["lsof", "-Pan", "-p", pid, "-iTCP", "-sTCP:LISTEN"],
        capture_output=True, text=True).stdout
    ports = [int(p) for p in re.findall(r":(\d+) \(LISTEN\)", lsof)]
    servers.append({"pid": int(pid), "exe": cmd.split()[0], "cmd": cmd,
                     "ports": ports})
  return servers


def _run_agentapi(exe: str, env_extra: dict, args: list[str],
                  timeout: int = 120) -> dict:
  env = {**os.environ, **env_extra}
  proc = subprocess.run(
      [exe, "agentapi", *args], env=env, capture_output=True, text=True,
      encoding="utf-8", timeout=timeout,
  )
  out = proc.stdout.strip() or proc.stderr.strip()
  try:
    data = json.loads(out)
  except json.JSONDecodeError:
    data = {"raw": out}
  if not data.get("error"):
    data.pop("error", None)
  if proc.returncode != 0 and not data.get("error"):
    data["error"] = f"agentapi exited {proc.returncode}"
  return data


def _load_session() -> dict:
  servers = [s for s in _list_servers() if "--csrf_token" in (s.get("cmd") or "")]
  if not servers:
    _die("Antigravity is not running. Ask the user to open the Antigravity app.")
  # Prefer the Antigravity agent app ("hub") over the Antigravity IDE.
  servers.sort(key=lambda s: "--subclient_type hub" not in s["cmd"])
  for srv in servers:
    token = re.search(r"--csrf_token[= ](\S+)", srv["cmd"]).group(1)
    for port in srv.get("ports") or []:
      env = {"ANTIGRAVITY_LS_ADDRESS": f"127.0.0.1:{port}",
             "ANTIGRAVITY_CSRF_TOKEN": token}
      probe = _run_agentapi(srv["exe"], env,
                            ["get-conversation-metadata", _PROBE_ID], timeout=15)
      err = str(probe.get("error", ""))
      if "Unavailable" not in err and "connection error" not in err:
        return {"exe": srv["exe"], "pid": srv["pid"], "env": env}
  _die("Found Antigravity but could not reach its agent API.")


def _agentapi(session: dict, *args: str, project: str | None = None,
              timeout: int = 120) -> dict:
  env = {**session["env"], "ANTIGRAVITY_PROJECT_ID": project or DEFAULT_PROJECT}
  return _run_agentapi(session["exe"], env, list(args), timeout=timeout)


def _find_conversation_id(data) -> str | None:
  """Pull the conversation id out of an agentapi response."""
  if isinstance(data, dict):
    for k in ("conversationId", "conversation_id"):
      if isinstance(data.get(k), str) and data[k]:
        return data[k]
    for v in data.values():
      found = _find_conversation_id(v)
      if found:
        return found
  return None


# --- commands ----------------------------------------------------------------

def cmd_status(_args) -> None:
  session = _load_session()
  print(json.dumps({
      "antigravity": "up",
      "pid": session["pid"],
      "address": session["env"]["ANTIGRAVITY_LS_ADDRESS"],
      "default_project": DEFAULT_PROJECT,
  }, indent=2))


def cmd_meta(args) -> None:
  print(json.dumps(_agentapi(_load_session(), "get-conversation-metadata",
                             args.conversation_id), indent=2))


def cmd_send(args) -> None:
  extra = [f"--title={args.title}"] if args.title else []
  print(json.dumps(_agentapi(_load_session(), "send-message", *extra,
                             args.conversation_id, args.message), indent=2))


def _load_tasks(args) -> list[dict]:
  defaults = {"model": args.model, "timeout": args.timeout,
              "workspace": args.workspace, "profile": args.profile,
              "project": args.project}
  if args.prompt:
    raw = [{"prompt": args.prompt}]
  else:
    with open(args.tasks, encoding="utf-8") as f:
      raw = json.load(f)
  if isinstance(raw, dict):
    defaults.update({k: v for k, v in raw.get("defaults", {}).items()
                     if v is not None})
    raw = raw["tasks"]
  tasks, seen = [], set()
  for i, t in enumerate(raw, 1):
    if not t.get("prompt"):
      _die(f"task #{i} has no prompt")
    task = {**defaults, **{k: v for k, v in t.items() if v is not None}}
    task["id"] = re.sub(r"[^\w.-]", "_", str(task.get("id") or f"task-{i}"))
    if task["id"] in seen:
      _die(f"duplicate task id {task['id']}")
    seen.add(task["id"])
    if task.get("workspace"):
      task["workspace"] = os.path.abspath(task["workspace"])
    tasks.append(task)
  return tasks


def _result_path(run_dir: str, task: dict) -> str:
  return os.path.join(run_dir, f"{task['id']}.md")


def _dispatch(session: dict, run_id: str, run_dir: str, task: dict) -> dict:
  ws = task.get("workspace")
  prompt = task["prompt"] + RESULT_INSTRUCTIONS.format(
      workspace_line=f"Work in this directory: {ws}\n" if ws else "",
      result_path=_result_path(run_dir, task),
      marker=DONE_MARKER,
  )
  title = task.get("title") or f"[agy {run_id}] {task['id']}"
  args = ["new-conversation", f"--model={task['model']}", f"--title={title}"]
  if task.get("profile"):
    args.append(f"--profile={task['profile']}")
  resp = _agentapi(session, *args, prompt, project=task.get("project"))
  return {
      "id": task["id"],
      "model": task["model"],
      "conversation_id": _find_conversation_id(resp),
      "dispatch_response": resp,
      "status": "error" if "error" in resp else "running",
      "error": resp.get("error"),
  }


def _read_result(path: str) -> str | None:
  try:
    with open(path, encoding="utf-8") as f:
      text = f.read()
  except OSError:
    return None
  stripped = text.lstrip("﻿").rstrip()
  if not stripped.endswith(DONE_MARKER):
    return None
  return stripped[: -len(DONE_MARKER)].rstrip()


def cmd_run(args) -> None:
  session = _load_session()
  tasks = _load_tasks(args)
  run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
  run_dir = os.path.join(RUNS_DIR, run_id)
  os.makedirs(run_dir, exist_ok=True)

  with cf.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
    results = list(pool.map(
        lambda t: _dispatch(session, run_id, run_dir, t), tasks))
  by_id = {r["id"]: r for r in results}
  for r in results:
    r["result_file"] = _result_path(run_dir, r)
    print(f"[{r['status']}] {r['id']} conv={r['conversation_id']}",
          file=sys.stderr, flush=True)

  if not args.no_wait:
    started = time.time()
    pending = {t["id"]: t for t in tasks if by_id[t["id"]]["status"] == "running"}
    while pending:
      for tid, task in list(pending.items()):
        output = _read_result(_result_path(run_dir, task))
        elapsed = time.time() - started
        if output is not None:
          by_id[tid].update(status="ok", output=output, seconds=round(elapsed))
        elif elapsed > task["timeout"]:
          by_id[tid].update(status="timeout", seconds=round(elapsed))
        else:
          continue
        print(f"[{by_id[tid]['status']}] {tid} ({round(elapsed)}s)",
              file=sys.stderr, flush=True)
        del pending[tid]
      if pending:
        time.sleep(args.poll)

  for r in results:
    if r["status"] == "ok":
      r.pop("dispatch_response", None)
  summary = {"run_id": run_id, "run_dir": run_dir, "results": results}
  with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)
  sys.stdout.reconfigure(encoding="utf-8")
  print(json.dumps(summary, indent=2, ensure_ascii=False))
  bad = [r for r in results if r["status"] not in ("ok", "running")]
  sys.exit(1 if bad else 0)


def cmd_collect(args) -> None:
  run_dir = os.path.join(RUNS_DIR, args.run_id)
  try:
    with open(os.path.join(run_dir, "summary.json"), encoding="utf-8") as f:
      summary = json.load(f)
  except FileNotFoundError:
    _die(f"no run {args.run_id} under {RUNS_DIR}")
  for r in summary["results"]:
    if r["status"] in ("running", "timeout"):
      output = _read_result(r["result_file"])
      if output is not None:
        r.update(status="ok", output=output)
        r.pop("dispatch_response", None)
  with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)
  sys.stdout.reconfigure(encoding="utf-8")
  print(json.dumps(summary, indent=2, ensure_ascii=False))


def main() -> None:
  p = argparse.ArgumentParser(description="Gemini Flash orchestrator (Antigravity)")
  sub = p.add_subparsers(dest="cmd", required=True)
  sub.add_parser("status").set_defaults(fn=cmd_status)

  m = sub.add_parser("meta")
  m.add_argument("conversation_id")
  m.set_defaults(fn=cmd_meta)

  c = sub.add_parser("collect")
  c.add_argument("run_id")
  c.set_defaults(fn=cmd_collect)

  s = sub.add_parser("send")
  s.add_argument("conversation_id")
  s.add_argument("message")
  s.add_argument("--title")
  s.set_defaults(fn=cmd_send)

  r = sub.add_parser("run")
  r.add_argument("tasks", nargs="?")
  r.add_argument("--prompt")
  r.add_argument("--workspace")
  r.add_argument("--model", default="flash", choices=["flash_lite", "flash", "pro"])
  r.add_argument("--profile")
  r.add_argument("--project",
                 help=f"Antigravity project id (default {DEFAULT_PROJECT})")
  r.add_argument("--timeout", type=int, default=900)
  r.add_argument("--concurrency", type=int, default=6)
  r.add_argument("--poll", type=float, default=5)
  r.add_argument("--no-wait", action="store_true",
                 help="dispatch and return; collect later from run_dir")
  r.set_defaults(fn=cmd_run)

  args = p.parse_args()
  if args.cmd == "run" and not (args.tasks or args.prompt):
    p.error("run needs a tasks file or --prompt")
  args.fn(args)


if __name__ == "__main__":
  main()
