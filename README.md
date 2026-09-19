# gemini-flash-orchestrator

A Claude Code / Codex skill that hands work to parallel Gemini Flash agents
running in your local, signed-in Google Antigravity app. It needs no API key.
See [SKILL.md](SKILL.md) for how it works.

## Download

Every push to `main` builds `gemini-flash-orchestrator.skill` and attaches it
to the **latest** release:

`https://github.com/rickyegl/gemini-flash-orchestrator/releases/download/latest/gemini-flash-orchestrator.skill`

A `.skill` file is a zip. Import it with your client's skill import, or unzip it
into `~/.claude/skills/` (or `~/.codex/skills/`).

## Build locally

```
python build.py   # writes dist/gemini-flash-orchestrator.skill
```

## Requirements

- Antigravity app open and signed in
- Python 3.8+
