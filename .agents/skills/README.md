# Agent skills

Shared, agent-neutral [Agent Skills](https://agents.md/) for Healthcare-GPT.
Each subdirectory is one skill: a `SKILL.md` with YAML frontmatter (`name` +
`description`) plus any supporting files. These are committed and shared with
the whole team.

The `SKILL.md` format is common across coding agents, so one folder serves all
of them — the only difference is where each agent looks for skills.

## Available skills

| Skill | What it does |
| --- | --- |
| [`create-issue`](create-issue/SKILL.md) | Draft and open a GitHub issue from a request or problem, matching this repo's issue style, with a copy-pasteable markdown fallback. |

> `create-issue` uses the [GitHub CLI](https://cli.github.com) (`gh`) to open
> the issue. `gh` is **optional**: if it isn't installed or authenticated, the
> skill instead prints the finished issue as copy-pasteable markdown for the
> GitHub web form. See [GitHub CLI (`gh`)](#github-cli-gh) below to enable
> one-step creation.

## Using these skills

### Codex — works out of the box

Codex scans `.agents/skills/` from your working directory up to the repo root,
so cloning the repo is all it takes. Skills can be invoked explicitly
(name them in your prompt) or implicitly (Codex matches your task to a skill's
`description`). No setup required.

### Claude Code — one-time local activation

Claude Code discovers skills under `.claude/skills/`, and this repo's `.claude/`
is gitignored (it holds per-developer settings), so we bridge to this folder.
**From the repository root**, run:

```bash
python scripts/install_skills.py
```

This points `.claude/skills` at `.agents/skills`. (The script finds the repo
root from its own location, so it works from any directory, but running it from
the root keeps things obvious.) Restart your Claude Code session afterward, then
run `/create-issue`.

> **Windows:** symlinks need Developer Mode or admin. If the command above fails
> with a permissions error, copy the skills instead — also from the repo root:
>
> ```powershell
> python scripts\install_skills.py --copy
> ```
>
> A copy is a snapshot, so re-run it after the skills change.

### Other agents

Any tool that reads [`AGENTS.md`](../../AGENTS.md) is pointed at this folder
there. Tools that support the `.agents/skills/` convention pick these up
automatically.

## GitHub CLI (`gh`)

The `create-issue` skill uses the [GitHub CLI](https://cli.github.com) to open
issues directly. It is **optional** — without it, the skill falls back to
printing copy-pasteable markdown — but installing it enables one-step creation.

Install it:

```bash
# macOS (Homebrew)
brew install gh

# Windows (winget)
winget install --id GitHub.cli

# Debian / Ubuntu
sudo apt install gh
```

Other platforms and package managers are covered in the
[official install guide](https://github.com/cli/cli#installation).

Then authenticate once and confirm:

```bash
gh auth login      # follow the prompts (GitHub.com → HTTPS or SSH)
gh auth status     # should report you're logged in
```

After that, `/create-issue` (Claude) or the `create-issue` skill (Codex) can
open issues without any extra steps.

## Adding a new skill

1. Create `.agents/skills/<skill-name>/SKILL.md` — `<skill-name>` must be
   lowercase kebab-case (`^[a-z0-9]+(-[a-z0-9]+)*$`), matching the `name:`
   frontmatter field.
2. Write a discriminating `description` — it is how agents decide *when* to use
   the skill, so state exactly when it should and should not trigger.
3. Commit. Codex users get it on pull; Claude users already symlinked, so it
   shows up after a session restart.
