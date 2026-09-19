---
name: gemini-flash-orchestrator
description: Delegate work to parallel Gemini Flash agents running in the user's local, signed-in Google Antigravity app (no API key). Use when the user asks to hand tasks to Gemini/Antigravity/Flash agents, fan out many independent subtasks (research, file-by-file reviews, test writing, bulk edits) to cheap workers, or get a second opinion from Gemini. The calling agent stays the orchestrator - it splits the work, dispatches it, waits, then verifies and merges results.
---

# Gemini Flash orchestrator (local Antigravity session)

Workers are real Antigravity conversations started through the app's
`agentapi`, so they use the user's Antigravity login and quota — never ask for
or set `GEMINI_API_KEY`. The script finds the running Antigravity app on its
own. Each worker is told to write its final answer to a result file; the
script waits for those files and returns JSON.

`AGY` below means: `python "<this skill's folder>/scripts/agy.py"` — e.g.
`~/.claude/skills/gemini-flash-orchestrator/...` (Claude Code) or
`~/.codex/skills/gemini-flash-orchestrator/...` (Codex). Expand `~` to the
user's home directory.

## 0. Preflight

```
AGY status
```

- `"antigravity": "up"` → continue.
- `Antigravity is not running` → ask the user to open the Antigravity app,
  then retry. Don't launch or sign in to it yourself.

## 1. Plan the split

Good worker tasks are independent, well scoped, and verifiable: "review
`src/auth/*.py` for injection bugs", "write unit tests for `parser.ts`",
"summarize these 3 docs". Avoid giving two workers the same files to edit.

Each prompt must stand alone — workers can't see this conversation. Include
goal, exact files/dirs, constraints, and the shape of the answer you want.
Pass the directory with `workspace`; it's added to the prompt as an absolute path.

Models: `flash` (default), `flash_lite` (trivial/bulk), `pro` (hard reasoning,
use sparingly).

## 2. Dispatch and wait

Single task:

```
AGY run --prompt "List every TODO in this repo with file:line" --workspace "C:/path/to/repo"
```

Many tasks — write a JSON file (a temp/scratch dir is fine) then run it:

```json
{
  "defaults": {"model": "flash", "workspace": "C:/path/to/repo", "timeout": 900},
  "tasks": [
    {"id": "review-auth", "prompt": "Review src/auth/ for security bugs. Report file:line, severity, fix."},
    {"id": "tests-parser", "prompt": "Add pytest tests for src/parser.py covering edge cases. Do not modify src/."},
    {"id": "docs", "model": "flash_lite", "prompt": "Summarize docs/*.md in 10 bullets."}
  ]
}
```

```
AGY run tasks.json --concurrency 6
```

Simple tasks finish in ~10-30 s. Run with a long shell timeout (up to 10 min)
or in the background; for longer jobs use `--no-wait`, then later
`AGY collect <run_id>`.

Output: `{"run_id", "run_dir", "results": [{id, status, output, conversation_id, result_file, ...}]}`.
Status is `ok`, `running` (no-wait), `timeout`, or `error`. Exit code 1 means
at least one task failed or timed out.

Workers run in the Antigravity project `outside-of-project` by default; use
`--project <id>` (or `"project"` per task, or env `AGY_PROJECT_ID`) for another
project from `~/.gemini/config/projects/`.

## 3. Follow up / inspect

- `AGY send <conversation_id> "Also check X; rewrite the result file when done."`
  then poll the task's `result_file` (delete the old file first; the worker
  must rewrite it ending with `<<AGY_DONE>>`).
- `AGY meta <conversation_id>` shows raw conversation config (useful on timeout).
- The user can open any worker in the Antigravity app; titles look like
  `[agy <run_id>] <task id>`.

## 4. Verify before trusting

Flash output is a draft. Before reporting to the user: read the diffs workers
made (`git diff`), run tests/linters, spot-check claims against the code, and
resolve conflicts between workers. Say which parts came from Gemini workers.

## Troubleshooting

- `timeout` with no result file: the worker may be stuck on a permission
  prompt in Antigravity (e.g. running a command). Ask the user to check the
  app, or re-run with a narrower task.
- `project_id is required`: pass a valid `--project`.
- `could not reach its agent API`: Antigravity may be starting up; wait and
  retry, or ask the user to restart it.
