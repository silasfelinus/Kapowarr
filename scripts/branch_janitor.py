#!/usr/bin/env python3
"""branch_janitor.py — keep Kapowarr's remote branch list clean.

Kapowarr accumulates stale `claude/*` / `worker/*` branches from agent sessions
and has no "delete head branch on merge" setting, so merged-PR branches pile up
with no cleanup path. Agent session credentials 403 on `git push origin --delete`
for this repo, so this runs from a GitHub Actions workflow whose GITHUB_TOKEN
*can* delete refs (.github/workflows/branch-janitor.yml).

Minimal equivalent of conductor's own scripts/branch_janitor.py, scoped down to
what Kapowarr actually needs: this repo doesn't need conductor's STRANDED-tier
age-based judgment logic (unmerged-but-stale reporting) — just an escape hatch
that auto-deletes branches already fully merged into main, plus a
workflow_dispatch "delete these named branches" override for confirmed-
superseded branches that (for whatever reason) aren't a strict ancestor of main
(e.g. squash-merged content that's present but not byte-identical history).

Two tiers only:
  - MERGED — a strict ancestor of origin/main (fully merged, nothing unique) -> delete.
  - FORCE  — named explicitly via --force-delete (operator/session already verified
             superseded, e.g. by diffing content against main) -> delete.

Everything else is left alone and reported, unclassified — no age heuristics.

It never creates commits or branches — delete-and-report only.

Usage:
  python scripts/branch_janitor.py [--dry-run] [--prefixes claude/,worker/]
      [--force-delete b1,b2] [--no-fetch] [--json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PREFIXES = ("claude/", "worker/")

MERGED = "merged"
FORCE = "force"
LEFT_ALONE = "left_alone"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()


def refresh_remotes() -> None:
    git("fetch", "origin", "+refs/heads/*:refs/remotes/origin/*", "--prune")


def list_remote_branches(prefixes: tuple[str, ...]) -> list[str]:
    """Return short branch names (no 'origin/') under the given prefixes, minus main."""
    raw = git("branch", "-r", "--format=%(refname:short)")
    out: list[str] = []
    for line in raw.splitlines():
        ref = line.strip()
        if not ref.startswith("origin/"):
            continue
        name = ref[len("origin/"):]
        if name in ("main", "HEAD") or "->" in ref:
            continue
        if any(name.startswith(p) for p in prefixes):
            out.append(name)
    return out


def list_all_remote_branches() -> list[str]:
    """Return every remote branch short name (no 'origin/'), minus main/HEAD — no prefix filter."""
    raw = git("branch", "-r", "--format=%(refname:short)")
    out: list[str] = []
    for line in raw.splitlines():
        ref = line.strip()
        if not ref.startswith("origin/"):
            continue
        name = ref[len("origin/"):]
        if name in ("main", "HEAD") or "->" in ref:
            continue
        out.append(name)
    return out


def is_merged(branch: str, base: str = "origin/main") -> bool:
    """True if branch tip is a strict ancestor of base (fully merged)."""
    rc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", f"origin/{branch}", base],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    ).returncode
    return rc == 0


def classify(
    branches: list[str],
    *,
    is_merged_fn,
    force_set: set[str],
) -> dict[str, list[str]]:
    """Pure classifier — inject is_merged_fn(branch)->bool.

    Precedence: FORCE (explicit operator/session intent) > MERGED > LEFT_ALONE.
    """
    result: dict[str, list[str]] = {MERGED: [], FORCE: [], LEFT_ALONE: []}
    for b in branches:
        if b in force_set:
            result[FORCE].append(b)
        elif is_merged_fn(b):
            result[MERGED].append(b)
        else:
            result[LEFT_ALONE].append(b)
    return result


def delete_branch(branch: str, dry_run: bool) -> bool:
    if dry_run:
        return True
    rc = subprocess.run(
        ["git", "push", "origin", "--delete", branch],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    ).returncode
    return rc == 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prune merged/superseded Kapowarr branches")
    parser.add_argument("--dry-run", action="store_true", help="Plan only; delete nothing")
    parser.add_argument("--prefixes", default=",".join(DEFAULT_PREFIXES),
                        help="Comma-separated branch prefixes to consider")
    parser.add_argument("--force-delete", default="",
                        help="Comma-separated branch names to delete regardless of merge state")
    parser.add_argument("--no-fetch", action="store_true", help="Skip git fetch (tests)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    if not args.no_fetch:
        refresh_remotes()

    prefixes = tuple(p for p in (s.strip() for s in args.prefixes.split(",")) if p)
    force_set = {b.strip() for b in args.force_delete.split(",") if b.strip()}
    branches = list_remote_branches(prefixes)

    # A forced name is explicit operator/session intent regardless of naming convention —
    # it must not be silently dropped just because it falls outside --prefixes.
    if force_set:
        existing = force_set & set(list_all_remote_branches())
        for b in existing:
            if b not in branches:
                branches.append(b)

    plan = classify(branches, is_merged_fn=is_merged, force_set=force_set)

    deleted, failed = [], []
    for b in plan[MERGED] + plan[FORCE]:
        (deleted if delete_branch(b, args.dry_run) else failed).append(b)

    summary = {
        "considered": len(branches),
        "deleted": deleted,
        "delete_failed": failed,
        "left_alone": plan[LEFT_ALONE],
        "dry_run": args.dry_run,
    }

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        verb = "Would delete" if args.dry_run else "Deleted"
        print(f"Considered {len(branches)} {'/'.join(prefixes)} branch(es).")
        print(f"{verb} (merged/forced): {', '.join(deleted) or '(none)'}")
        if failed:
            print(f"Delete FAILED (perms?): {', '.join(failed)}")
        if plan[LEFT_ALONE]:
            print(f"Left alone (unmerged, not forced): {', '.join(plan[LEFT_ALONE])}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
