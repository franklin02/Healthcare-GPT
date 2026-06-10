"""Activate the repo's shared agent skills for Claude Code.

The shared, agent-neutral skills live in the committed ``.agents/skills/``
folder. Codex reads that location natively, so it needs no setup. Claude Code,
however, discovers skills under ``.claude/skills/``, and this repo's ``.claude/``
is gitignored (it holds per-developer settings). This script bridges the two by
pointing ``.claude/skills`` at ``.agents/skills``.

Usage (run from anywhere — the script finds the repo root from its own
location)::

    python scripts/install_skills.py           # symlink (preferred)
    python scripts/install_skills.py --copy     # copy instead of symlink

Prefer the default symlink so the skills stay in sync automatically. Use
``--copy`` only when symlinks are unavailable (typically Windows without
Developer Mode/admin); a copy is a snapshot, so re-run after the skills change.

Idempotent and safe to run repeatedly. Restart your Claude Code session
afterward so the skills are picked up. Codex users do not need to run this.
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
CLAUDE_DIR = REPO_ROOT / ".claude"
LINK_PATH = CLAUDE_DIR / "skills"
# Relative target so the symlink stays valid if the repo moves.
LINK_TARGET = Path("..") / ".agents" / "skills"


def _clear_existing() -> bool:
    """Remove an existing .claude/skills we manage. Return False if unsafe."""
    if LINK_PATH.is_symlink():
        LINK_PATH.unlink()
    elif LINK_PATH.is_dir():
        shutil.rmtree(LINK_PATH)
    elif LINK_PATH.exists():
        print(
            f"error: {LINK_PATH} exists and is not a folder or symlink.\n"
            "Move or remove it, then re-run."
        )
        return False
    return True


def install_symlink() -> int:
    if LINK_PATH.is_symlink() and LINK_PATH.resolve() == SKILLS_DIR.resolve():
        print(f"ok: {LINK_PATH} already points to {SKILLS_DIR}")
        return 0
    if not _clear_existing():
        return 1
    try:
        os.symlink(LINK_TARGET, LINK_PATH, target_is_directory=True)
    except OSError as exc:
        print(
            f"error: could not create symlink {LINK_PATH} -> {LINK_TARGET}: {exc}\n"
            "On Windows this usually needs Developer Mode or admin. Either enable\n"
            "those, or copy the skills instead:\n"
            "    python scripts/install_skills.py --copy"
        )
        return 1
    print(f"ok: linked {LINK_PATH} -> {SKILLS_DIR}")
    print("Restart your Claude Code session, then run /create-issue.")
    return 0


def install_copy() -> int:
    if not _clear_existing():
        return 1
    shutil.copytree(SKILLS_DIR, LINK_PATH)
    print(f"ok: copied {SKILLS_DIR} -> {LINK_PATH}")
    print("Note: this is a snapshot — re-run with --copy after skills change.")
    print("Restart your Claude Code session, then run /create-issue.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--copy",
        action="store_true",
        help="copy .agents/skills into .claude/skills instead of symlinking "
        "(use when symlinks are unavailable, e.g. Windows without Developer Mode)",
    )
    args = parser.parse_args()

    if not SKILLS_DIR.is_dir():
        print(f"error: shared skills folder not found at {SKILLS_DIR}")
        return 1

    CLAUDE_DIR.mkdir(exist_ok=True)
    return install_copy() if args.copy else install_symlink()


if __name__ == "__main__":
    raise SystemExit(main())
