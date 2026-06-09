"""Activate the repo's shared agent skills for Claude Code.

The shared, agent-neutral skills live in the committed ``.agents/skills/``
folder. Codex reads that location natively, so it needs no setup. Claude Code,
however, discovers skills under ``.claude/skills/``, and this repo's ``.claude/``
is gitignored (it holds per-developer settings). This script bridges the two by
creating a ``.claude/skills`` symlink pointing at ``.agents/skills``.

Usage::

    python scripts/install_skills.py

Idempotent and safe to run repeatedly. Restart your Claude Code session
afterward so the skills are picked up. Codex users do not need to run this.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
CLAUDE_DIR = REPO_ROOT / ".claude"
LINK_PATH = CLAUDE_DIR / "skills"
# Relative target so the symlink stays valid if the repo moves.
LINK_TARGET = Path("..") / ".agents" / "skills"


def main() -> int:
    if not SKILLS_DIR.is_dir():
        print(f"error: shared skills folder not found at {SKILLS_DIR}")
        return 1

    CLAUDE_DIR.mkdir(exist_ok=True)

    if LINK_PATH.is_symlink():
        if LINK_PATH.resolve() == SKILLS_DIR.resolve():
            print(f"ok: {LINK_PATH} already points to {SKILLS_DIR}")
            return 0
        print(
            f"error: {LINK_PATH} is a symlink to {os.readlink(LINK_PATH)!r}.\n"
            "Remove it and re-run."
        )
        return 1

    if LINK_PATH.exists():
        print(
            f"error: {LINK_PATH} already exists and is not a symlink.\n"
            "Move or remove it, then re-run (it would shadow the shared skills)."
        )
        return 1

    try:
        os.symlink(LINK_TARGET, LINK_PATH, target_is_directory=True)
    except OSError as exc:
        print(
            f"error: could not create symlink {LINK_PATH} -> {LINK_TARGET}: {exc}\n"
            "On Windows, enable Developer Mode or run as admin, or copy "
            "'.agents/skills/' to '.claude/skills/' manually."
        )
        return 1

    print(f"ok: linked {LINK_PATH} -> {SKILLS_DIR}")
    print("Restart your Claude Code session, then run /create-issue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
