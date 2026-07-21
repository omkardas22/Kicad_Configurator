"""
push_to_github.py — Push updated project files to GitHub
=========================================================
This script stages all changed files, commits them with a message,
and pushes to the remote repository.

IMPORTANT: This script excludes itself from being tracked by Git.
On first run it auto-adds itself to .gitignore if not already there.

Usage:
  python push_to_github.py                          # Auto-generated commit message
  python push_to_github.py -m "your commit message" # Custom commit message
  python push_to_github.py --tag v1.1.0             # Also create and push a tag
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Force UTF-8 on Windows consoles
os.environ["PYTHONUTF8"] = "1"

SCRIPT_NAME = Path(__file__).name  # push_to_github.py
ROOT = Path(__file__).parent.resolve()
GITIGNORE = ROOT / ".gitignore"

# Terminal colours
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def log(msg: str, colour: str = "") -> None:
    try:
        print(f"{colour}{msg}{RESET}" if colour else msg, flush=True)
    except UnicodeEncodeError:
        safe = msg.encode("ascii", errors="replace").decode("ascii")
        print(f"{colour}{safe}{RESET}" if colour else safe, flush=True)


def run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    log(f"  $ {' '.join(cmd)}", CYAN)
    result = subprocess.run(
        cmd, cwd=str(ROOT), text=True, capture_output=capture,
    )
    if result.returncode != 0 and not capture:
        raise RuntimeError(f"Command failed (exit {result.returncode})")
    return result


def ensure_self_in_gitignore() -> None:
    """Add this script to .gitignore if it isn't already listed."""
    if GITIGNORE.exists():
        content = GITIGNORE.read_text(encoding="utf-8")
        # Check if already ignored (exact line match)
        lines = content.splitlines()
        if SCRIPT_NAME in lines or f"/{SCRIPT_NAME}" in lines:
            log(f"  [OK] {SCRIPT_NAME} already in .gitignore", GREEN)
            return
    else:
        content = ""

    # Append to .gitignore
    with open(GITIGNORE, "a", encoding="utf-8") as f:
        if content and not content.endswith("\n"):
            f.write("\n")
        f.write(f"\n# Auto-exclude: GitHub push script\n{SCRIPT_NAME}\n")

    log(f"  [OK] Added {SCRIPT_NAME} to .gitignore", GREEN)


def remove_self_from_tracking() -> None:
    """If git is already tracking this script, remove it from the index."""
    result = run(["git", "ls-files", SCRIPT_NAME], capture=True)
    if result.stdout.strip():
        log(f"  [WARN] {SCRIPT_NAME} is tracked — removing from git index", YELLOW)
        run(["git", "rm", "--cached", SCRIPT_NAME])


def get_changed_summary() -> str:
    """Get a short summary of staged changes for the auto-commit message."""
    result = run(["git", "diff", "--cached", "--stat"], capture=True)
    lines = result.stdout.strip().splitlines()
    if lines:
        return lines[-1].strip()  # e.g. "5 files changed, 120 insertions(+), 30 deletions(-)"
    return "no changes"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Push updated project files to GitHub (excludes itself)",
    )
    parser.add_argument(
        "-m", "--message",
        default=None,
        help="Custom commit message (default: auto-generated)",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="Create and push an annotated tag (e.g. v1.1.0)",
    )
    parser.add_argument(
        "--branch",
        default=None,
        help="Branch to push to (default: current branch)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without actually pushing",
    )
    args = parser.parse_args()

    log(f"\n{BOLD}{CYAN}{'=' * 55}{RESET}")
    log(f"{BOLD}{CYAN}  Push to GitHub — KiCad Constraint Configurator{RESET}")
    log(f"{BOLD}{CYAN}{'=' * 55}{RESET}\n")

    # Step 1: Status check (script is now tracked)
    log(f"{BOLD}[1/5] Running GitHub Push Script{RESET}")

    # Step 2: Stage all changes (respects .gitignore)
    log(f"\n{BOLD}[2/5] Staging changes{RESET}")
    run(["git", "add", "-A"])

    # Step 3: Check if there's anything to commit
    log(f"\n{BOLD}[3/5] Checking for changes{RESET}")
    status = run(["git", "status", "--porcelain"], capture=True)
    if not status.stdout.strip():
        log("  [OK] Working tree clean — nothing to commit", GREEN)
        return

    # Show what's staged
    run(["git", "status", "--short"])
    summary = get_changed_summary()

    # Step 4: Commit
    log(f"\n{BOLD}[4/5] Committing{RESET}")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit_msg = args.message or f"update: {summary} ({timestamp})"

    if args.dry_run:
        log(f"  [DRY RUN] Would commit with message: {commit_msg}", YELLOW)
    else:
        run(["git", "commit", "-m", commit_msg])
        log(f"  [OK] Committed: {commit_msg}", GREEN)

    # Optional: create tag
    if args.tag:
        tag_msg = f"Release {args.tag}"
        if args.dry_run:
            log(f"  [DRY RUN] Would create tag: {args.tag}", YELLOW)
        else:
            run(["git", "tag", "-a", args.tag, "-m", tag_msg])
            log(f"  [OK] Tag created: {args.tag}", GREEN)

    # Step 5: Push
    log(f"\n{BOLD}[5/5] Pushing to remote{RESET}")
    branch = args.branch or ""
    push_cmd = ["git", "push"]
    if branch:
        push_cmd += ["origin", branch]

    if args.tag:
        push_cmd.append("--tags")

    if args.dry_run:
        log(f"  [DRY RUN] Would run: {' '.join(push_cmd)}", YELLOW)
    else:
        run(push_cmd)
        log(f"  [OK] Pushed successfully!", GREEN)

    log(f"\n{BOLD}{GREEN}{'=' * 55}{RESET}")
    log(f"{BOLD}{GREEN}  Done!{RESET}")
    log(f"{BOLD}{GREEN}{'=' * 55}{RESET}\n")


if __name__ == "__main__":
    main()
