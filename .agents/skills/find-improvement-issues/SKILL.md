---
name: find-improvement-issues
description: >-
  Inspect this repository for evidence-backed cleanup, tech-debt, docs/docstring,
  test-gap, and refactor opportunities, then draft GitHub issues sized as
  research-group work items. Surfaces work of every depth — from quick, almost
  mindless fixes to ones needing real investigation and testing — so there's
  something for everyone on the team. Every drafted issue traces to real tool
  output or a real file:line; never invents paths, labels, or problems. Use when
  asked to find tech debt, cleanup or refactor opportunities, docstring/docs
  fixes, test gaps, backlog work items, or "find work for the team." Drafts
  issues for review and only creates them with `gh` after explicit confirmation.
allowed-tools: Bash, Read, Grep, Glob
---

# FindImprovementIssues

Scan the repository for real, located improvement opportunities and turn the
strongest ones into well-scoped GitHub issues for the research group. The point
is to **find work for everyone**: some issues are quick and almost mindless
(a typo, a missing docstring, an unused import), others demand investigation,
design thought, and testing. Both are valuable — file whatever the evidence
supports, regardless of how deep it goes.

This skill is **evidence-first**: every candidate cites real tool output or a
real `file:line`. Broad "find tech debt" prompts otherwise produce generic or
hallucinated suggestions. It produces **work items, not code edits**, and never
files issues without explicit confirmation.

## When to use

- The user asks to find tech debt, cleanup, refactor, or backlog opportunities.
- The user wants docstring/doc fixes, test gaps, or hygiene work surfaced.
- The user says "find work for the team" / "what could people pick up."

Do **not** use this to fix code (it only drafts issues) or to file an issue from
a specific user-described problem — that's [`create-issue`](../create-issue/SKILL.md).

## Workflow

### 0. Orient (read-only)

- `git remote -v` — confirm you're in Healthcare-GPT, not another checkout.
- `gh label list` — capture existing labels: the provenance label
  (`claude-generated` / `codex-generated` / `ai-generated`) and any effort/area
  labels (`good first issue`, `documentation`, `GDELT`, etc.). Use what exists;
  never invent.
- `gh issue list --state open --limit 100` — for dedup and to mirror the repo's
  title/body conventions.
- Read `pyproject.toml`, `Makefile`, and `.github/workflows/*` to confirm the
  canonical commands. **Documented default for Healthcare-GPT:**
  `ruff format --check .`, `ruff check .`, `pytest -q`, plus `mypy`/`pyright`
  if configured.

### 1. Gather evidence (every candidate must cite a source)

Run a finite, read-only set of probes. Each hit is a *located* candidate:

- **Linter / type output:** `ruff check .` and the configured type checker.
  Each warning is a candidate with a file:line.
- **Code markers:** `grep -rn "TODO\|FIXME\|HACK\|XXX\|DEPRECATED" --include='*.py'`
  (exclude generated dirs).
- **Test signals:** skipped / `xfail` tests; public modules with no matching
  `tests/test_*.py`; coverage gaps if coverage is configured.
- **Docstring / doc gaps:** public functions/classes missing docstrings (ruff
  `D` rules if enabled, else a targeted check); stale README sections; broken
  doc links (only when the issue touches docs).
- **Duplication / dead code:** obvious copy-paste; unused imports/vars (ruff
  `F`); `vulture` if available.

**Exclude** `__pycache__`, `.ruff_cache`, `.pytest_cache`, `docs/_build`,
vendored code, and data fixtures — unless the issue is *specifically* about repo
hygiene.

### 2. Rank

Score candidates by **confidence** (is the evidence unambiguous?), **impact**,
**risk**, and **effort/depth**. Spread the final set across depths so the
backlog has both grab-and-go items and meaty ones.

### 3. Draft 3–5 issues

Soft cap — file fewer if fewer are justified, and **never pad**. Group related
findings into one issue rather than five near-duplicates. Anything whose
evidence is weak goes in a **"Not enough evidence (not filed)"** bucket instead
of becoming an issue.

Skip anything already covered by an open issue from step 0 (note the dedup).

### 4. Show all drafts for confirmation

Present every draft (title, rendered body, chosen labels) plus the
"Not enough evidence" bucket. **Do not call `gh issue create` until the user
approves.**

### 5. Create approved issues — follow `create-issue`

Once approved, create each issue using the exact same rules as the
[`create-issue`](../create-issue/SKILL.md) skill (its steps 5–7) so both skills
behave identically. In particular:

- **Title:** `<type>: <concise imperative summary>` — Conventional-Commit type,
  lowercase after the colon, no trailing period. Never add the type as a label.
- **Labels — existing labels only (`gh label list`):** apply only labels that
  already exist AND are clearly relevant. Typical mappings: `feat` →
  `enhancement`, a defect → `bug`, `docs` → `documentation`, GDELT work →
  `GDELT`; a priority label only if the user signals urgency. Add an effort
  label only if a matching one exists. **Never create a new label.**
- **Provenance label is required:** always include the agent's provenance label
  (`claude-generated` for Claude Code, `codex-generated` for Codex). If it is
  **missing from the repo**, do not substitute another agent's label and do not
  create the issue through `gh` — show the draft with a note that the label must
  be added to the repo first. Only create the label with explicit user consent.
- **Preflight, then create:** `command -v gh` and `gh auth status`. If either
  fails, skip `gh` and print each issue as a copy-pasteable ```md block (Title,
  body, Labels line) for `https://github.com/<owner>/<repo>/issues/new` — derive
  `<owner>/<repo>` from the `origin` remote. Otherwise:

  ```bash
  gh issue create --title "<title>" --body "<body>" [--label "<label>" ...]
  ```

Report each issue URL.

## Issue template

```md
Title: <conventional-commit prefix>: <concise summary>
       e.g. test(auth): add coverage for token refresh

## Problem
<what's wrong + evidence: a real file:line, or the exact tool output / grep hit>

## Suggested fix
<concrete, minimal approach — omit this heading entirely if the path is unclear>

## Checklist items
- [ ] teammate-sized step
- [ ] ...

## Validation
<exact command that proves it's done — e.g. `ruff check src/foo.py` is clean,
or `pytest tests/test_foo.py` passes>

Effort: quick | moderate | deep
```

`## Problem` and `## Validation` are required. `## Suggested fix` is optional —
omit the whole heading when the path isn't clear (never write "N/A").

## Effort tiers (map to labels only if they exist)

These describe *how much thinking a task needs*, not who's allowed to take it —
anyone can pick up anything.

- **Quick:** docstrings, docs polish, typo/lint fixes, unused-import removal,
  naming clarity, isolated cleanup. Low context, little testing.
- **Moderate:** small refactors, duplicate-logic removal, focused
  validation/error-handling, adding a missing test module.
- **Deep:** module-boundary or design cleanup, behavior-touching refactors —
  needs investigation and real test coverage. File these only when the evidence
  is strong.

## Guardrails

- **Never edit code** — this skill produces work items, not commits.
- **Never create issues without explicit confirmation.**
- **No invented paths, labels, bugs, or architecture problems** — cite a real
  `file:line` or real tool output for every claim.
- **Provenance label:** follow `create-issue` — always apply the agent's
  provenance label; if it's missing from the repo, don't create via `gh`, show
  the draft with a note. Only create the label with user consent.
- **Bound the scan** — a finite set of read-only commands; don't loop forever.

## Output discipline

The deliverables are the drafts and (after approval) the issue URLs. Keep
commentary minimal beyond the confirmation step and the dedup/"not filed" notes.
