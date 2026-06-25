---
name: create-issue
description: Draft and open a GitHub issue from a user's request, bug report, or feature idea (never from a git diff), matching this repository's conventions — a Conventional-Commit title prefix, a ## Problem section, an optional ## Suggested fix, a ## Checklist items list, and the required AI provenance label (`codex-generated` or `claude-generated`). Inspects the codebase and existing repo labels first, shows the draft for confirmation, then runs `gh issue create`; falls back to a copy-pasteable markdown block if creation fails. Use when the user asks to file, open, create, track, or "make an issue" out of a problem or feature.
allowed-tools: Bash, Read, Grep, Glob
---

# CreateIssue

Turn a user's request, bug report, or feature idea into a well-formed GitHub
issue that matches this repository's established style, then create it with
`gh`.

This skill generates issues from a **problem or request** — never from a git
diff. PR descriptions are based on committed changes and are out of scope for
this skill.

## When to use

- The user asks to "file / open / create / make an issue", "track this", or
  "write this up as an issue".
- The user describes a bug, feature, or task they want recorded.

## Procedure

### 1. Understand the request

Identify the concrete problem or feature to track. If the request is too vague
to write a useful Problem statement — you cannot name the behavior, the impact,
or the affected area — ask ONE focused clarifying question, then continue.
Otherwise proceed without further questions.

### 2. Inspect the codebase first (required)

Before drafting, ground the issue in the real code:

- Use Grep / Glob / Read to locate the files, functions, modules, APIs,
  database tables, or services the issue concerns.
- Collect concrete references to cite (e.g. `src/shared_utils.py:792`, a
  function name, a config key). Issues in this repo routinely cite
  `file.py:line` and short code snippets — do the same when it sharpens the
  issue.
- Never invent file paths or symbols. Only reference things you verified exist.

### 3. Choose exactly one issue type

Pick the single most accurate Conventional-Commit type for the title prefix:

`build` · `ci` · `docs` · `feat` · `fix` · `perf` · `refactor` · `test`

Repo notes: defects use `fix` (the repo has also used `bug:` — either is
acceptable, prefer `fix`); pure formatting or renames have used `style:`.
Choose ONE type; never combine them.

### 4. Write the issue (match repo style exactly)

**Title:** `<type>: <concise imperative summary>` — lowercase after the colon,
no trailing period. Example: `feat: add retry/backoff to the GDELT downloader`.

**Body** — use this structure:

```md
## Problem
<Current behavior, why it matters, the impact, and the affected components or
files. Cite verified `path:line` references and short code snippets where they
sharpen the issue.>

## Suggested fix
<Implementation direction. INCLUDE this section ONLY when the path is
reasonably clear; OMIT the whole heading when it is not — do not write "N/A".
A short paragraph or bullet list is fine.>

## Checklist items
- [ ] <verifiable, outcome-oriented item>
- [ ] <add "All tests passing", "Ruff format", or "Updated documentation" when relevant>
```

Rules:

- `## Problem` is required.
- `## Suggested fix` is optional — omit the entire heading when the
  implementation is unclear.
- `## Checklist items` holds concrete, verifiable steps a reviewer can check
  off; 2–6 items is typical.
- Keep it concise and implementation-focused. No filler.

Formatting: 
- never hard-wrap prose. Write each paragraph as a single
continuous line and let GitHub reflow it to the reader's width. Do not insert
mid-paragraph newlines to wrap at ~80 columns — on GitHub that renders as ragged
short lines with a big empty gutter on the right. Use blank lines only to separate
paragraphs, list items, and headings. (Bullet/checklist items are one line each;
sub-points can nest, but don't wrap a single point across lines.)

### 5. Choose labels from EXISTING labels only

- Run `gh label list` and apply only labels that already exist AND are clearly
  relevant.
- Always include the AI provenance label for the current agent:
  `codex-generated` for Codex-created issues, or `claude-generated` for Claude
  Code-created issues. These labels have the same meaning: the issue was
  AI-generated and needs human validation before it is treated as authoritative.
- Never create a new label. Never add the Conventional-Commit type as a label —
  the type lives in the title.
- Typical mappings when relevant: `feat` → `enhancement`; a defect → `bug`;
  `docs` → `documentation`; work in the GDELT area → `GDELT`. Apply a priority
  label (`priority-low` / `priority-medium` / `priority-high`) only if the user
  signals urgency.
- If the expected AI provenance label is missing, do not substitute a different
  agent label and do not create the issue through `gh`. Show the draft with a
  note that the required label must be added to the repository first. If no
  topical labels clearly apply, attach only the AI provenance label.

### 6. Preflight, confirm, then create

1. Show the user the final draft: the title, the rendered body, and the chosen
   labels.
2. Ask for confirmation before creating — opening an issue is an outward-facing
   action.
3. On approval, preflight the GitHub CLI before running anything that opens an
   issue:
   - `command -v gh` — is `gh` installed?
   - `gh auth status` — is it authenticated to the right host?
   If either check fails, skip creation and go straight to the fallback in
   step 7 — do not run a command you already expect to error.
4. When both checks pass, create it:
   ```bash
   gh issue create --title "<title>" --body "<body>" [--label "<label>" ...]
   ```
   Report the issue URL that `gh` prints. If the command still fails (network,
   a label that does not exist, no remote, etc.), fall back to step 7.

### 7. Fallback when `gh` is unavailable

`gh` is optional — the skill must still produce a usable result without it. If
`gh` is missing, unauthenticated, or `gh issue create` fails for any reason, do
NOT stop with an error. Instead:

1. Output a single fenced ` ```md ` block containing the complete issue — a
   `Title:` line, the full body, and a `Labels:` line listing the suggested
   labels — so the user can paste it straight into GitHub's new-issue form at
   `https://github.com/<owner>/<repo>/issues/new` (derive `<owner>/<repo>` from
   the `origin` remote when available).
2. Briefly state why it fell back and how to enable one-step creation next time:
   install the GitHub CLI (<https://cli.github.com>) and run `gh auth login`.

## Output discipline

Apart from the confirmation step and the final URL (or the fallback block), keep
commentary minimal. The deliverable is the issue, not an explanation of it.
