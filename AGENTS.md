# AGENTS.md

Guidance for AI coding agents (Codex, Claude Code, and others) working in
Healthcare-GPT.

## Shared agent skills

Reusable, agent-neutral skills live in [`.agents/skills/`](.agents/skills/).
Each is a directory with a `SKILL.md` describing when and how to use it. Prefer
an existing skill when a task matches its description.

- **`create-issue`** — turn a request, bug report, or feature idea into a
  GitHub issue that matches this repo's conventions, then open it with `gh`.
  See [`.agents/skills/create-issue/SKILL.md`](.agents/skills/create-issue/SKILL.md).

Discovery by agent:

- **Codex** scans `.agents/skills/` automatically — no setup.
- **Claude Code** reads `.claude/skills/`; run `python scripts/install_skills.py`
  once to bridge it to `.agents/skills/`, then restart the session.

See [`.agents/skills/README.md`](.agents/skills/README.md) for details and for
how to add a new skill.

## Project conventions

- **Issue / PR titles** use a Conventional-Commit prefix: `feat:`, `fix:`,
  `docs:`, `refactor:`, `perf:`, `test:`, `build:`, `ci:` (the repo has also
  used `bug:` and `style:`). Issue bodies use `## Problem`, an optional
  `## Suggested fix`, and a `## Checklist items` list.
- Labels are applied from the repository's existing label set only; the
  Conventional-Commit type is **not** a label (it lives in the title).
- Issues created by an AI agent must also carry the matching provenance label:
  `codex-generated` for Codex or `claude-generated` for Claude Code. Both
  labels mean the issue was AI-generated and needs human validation.
