# Fork maintenance and upstream refresh workflow

This document is specific to Silas Felinus's fork (`silasfelinus/Kapowarr`). It
explains how to check upstream ([`Casvt/Kapowarr`](https://github.com/Casvt/Kapowarr))
for new work, pull useful changes into the fork without losing fork-only
customizations, resolve the conflicts that come up, verify the result, and
keep attribution intact. It is not upstream documentation — see
[`CONTRIBUTING.md`](./CONTRIBUTING.md) for that project's own contribution
process, which still applies if you ever want to send something back
upstream.

## Why this exists

The fork's `main` branch is the only branch that matters here: every fork
change (personality/QoL tweaks, the Usenet/NZB download-client and indexer
support, hardening, this doc, etc.) lands there directly via reviewed PRs,
never on a separate long-lived fork branch. Upstream, by contrast, does its
active development on `development` and ships releases from `main`
(currently tagged `V1.3.1`). Those two `main` branches are unrelated after
the fork point — the fork's `main` is expected to accumulate its own commits
that upstream's `main` will never have — so "staying in sync" means
deliberately importing specific upstream changes when they're useful, not
keeping the branches identical.

## 1. Fetch and compare upstream

Add upstream as a second remote once (this is a one-time step per clone —
`origin` should stay pointed at the fork so pushes go to the right place):

```bash
git remote add upstream https://github.com/Casvt/Kapowarr.git
```

Fetch upstream's branches and tags without touching any local branch:

```bash
git fetch upstream
```

Find the point where the fork diverged from upstream, and how far each side
has moved since:

```bash
git merge-base origin/main upstream/main
git rev-list --count <merge-base>..origin/main     # fork-only commits
git rev-list --count <merge-base>..upstream/main    # upstream-only commits
```

See what upstream has done since the fork point, one commit per line:

```bash
git log --oneline <merge-base>..upstream/main
```

For a specific area (e.g. before importing a change touching download
clients), read the actual diff rather than just the log:

```bash
git diff origin/main..upstream/main -- backend/implementations/
```

Upstream's `development` branch is where its next release is actively built
(ahead of `main`, not yet tagged) — check it too if you want to catch a fix
before its next release, but treat it as less stable than `upstream/main`.

## 2. Decide what to pull in, and keep fork customizations isolated

Not every upstream commit is worth taking, and not every fork commit is
compatible with a naive merge. Before pulling anything in:

- **Read the commit(s), don't just merge blind.** A one-line upstream bugfix
  in a file the fork hasn't touched is safe to take as-is. A change to a file
  the fork has meaningfully diverged in (e.g. `backend/features/download_queue.py`,
  which now has NZB/Usenet branches upstream doesn't have) needs a real read
  of both sides before deciding whether to merge, cherry-pick, or manually
  port just the relevant part.
- **Cherry-pick for a single targeted fix**, merge for a broader catch-up:
  ```bash
  git cherry-pick <upstream-commit-sha>
  ```
  A cherry-pick keeps the fork's history linear and makes it obvious exactly
  what was imported and why (put the reason in the commit message or the PR
  description if it isn't already clear from the upstream commit itself).
- **Merge for a larger catch-up** (e.g. after upstream cuts a new release):
  ```bash
  git checkout -b upstream-sync-<date>
  git merge upstream/main
  ```
  Do this on a branch, never directly on `main` — resolve conflicts there,
  verify (see step 3), and open a normal PR into the fork's `main` so it goes
  through the same review as any other change.
- **Where fork-only code lives**: there's no separate "fork customizations"
  directory to keep isolated — the fork's changes are ordinary commits
  spread across the codebase (frontend labels/branding, `backend/implementations/`
  additions for SABnzbd and Newznab indexers, notification services, the
  health-check panel, etc.). The isolation that matters is at the *commit*
  level: keep fork-specific work in its own commits with clear messages
  (already the norm — see `git log --oneline`) rather than folding it into
  the same commit as an upstream import, so a future `git log <merge-base>..HEAD`
  or `git blame` can still tell fork-original work apart from imported
  upstream work.
- **README and branding**: the fork notice at the top of `README.md`, the
  `ghcr.io/silasfelinus/kapowarr` container reference, and the Docker
  Compose file's image pointer are fork-specific and should never be
  overwritten by an upstream merge/cherry-pick. If a merge touches
  `README.md` or `docker-compose.yml`, check the fork-specific parts
  survived before committing the merge.

## 3. Resolve conflicts

Standard git conflict resolution applies — nothing fork-specific about the
mechanics. The judgment call is *which side to prefer* when both have
touched the same code:

- **Fork-only files/areas** (e.g. anything under the Usenet/NZB indexer and
  download-client code, the notification system, the health-check panel):
  keep the fork's version. Upstream doesn't have this code, so a conflict
  here almost always means upstream added something adjacent — merge both
  rather than picking one side.
- **Shared files upstream also changed** (e.g. `backend/base/file_extraction.py`,
  matching/search logic): read both diffs, and default to taking upstream's
  fix/improvement while re-applying any fork-specific behavior on top, rather
  than blindly keeping the fork's older version. Upstream bugfixes in shared
  logic are exactly the kind of thing worth importing.
- **Branding/identity files** (`README.md` fork notice, `docker-compose.yml`
  image, any "personality" strings the fork intentionally changed): keep the
  fork's version unless the upstream conflict is a genuine content change
  unrelated to branding, in which case merge the two by hand.
- If a conflict is genuinely ambiguous (both sides changed the same logic in
  incompatible ways and picking one loses real behavior), don't guess —
  describe the conflict in the PR description and leave it for review rather
  than silently resolving it one way.

Never force-push over a rejected push to resolve a conflict; fetch the
branch's current remote tip, merge or rebase it in, re-resolve, and push
normally. If a plain (non-force) push is rejected, that's the safety net
doing its job.

## 4. Run the tests

The fork keeps upstream's test/lint tooling and CI (`.github/workflows/tests.yml`,
`.github/workflows/deploy.yml` for the docs build, `.github/workflows/container.yml`
for the published image) unchanged. Run the same checks locally before
opening a PR, whether the change came from upstream or is fork-original:

```bash
pip install -r requirements.txt -r requirements-dev.txt

# Type checking
mypy --explicit-package-bases .

# Import sorting (checks only; drop --check to actually sort)
isort --check-only .

# Style (checks only; drop --diff/--in-place to apply)
autopep8 --recursive --diff .

# Full test suite (mirrors what CI runs, across Python 3.8-3.12)
mkdir -p db
python -m unittest discover -s ./tests -p '*.py'
```

All of the above must pass before merging, same bar as any other change to
this repo (see `CONTRIBUTING.md`'s "Strict rules"). CI runs the same
`unittest` suite across Python 3.8 through 3.12, plus a `mkdocs build` check
on `docs/mkdocs.yml` (upstream's documentation site config, which this fork
still ships but has not customized) and the container build/publish
workflow. A merge or cherry-pick from upstream is not "safe to skip testing"
just because it passed CI upstream — the fork's surrounding code has
diverged enough (Usenet/NZB support in particular) that the same change can
behave differently here.

## 5. Preserve attribution

This fork exists because of upstream's work, and that has to stay visible
regardless of how much fork-specific code gets added:

- Keep the `GPL-3.0` `LICENSE` file as-is — do not relicense.
- Keep the fork notice at the top of `README.md` (the "Personal fork" callout
  linking to `Casvt/Kapowarr`) intact through any merge or rewrite of the
  surrounding README content.
- Keep the upstream links in `README.md` (issues, Discord, docs hub, Ko-Fi)
  pointing at upstream, not the fork, unless the fork later stands up its own
  equivalents.
- When importing an upstream commit via cherry-pick or merge, keep upstream's
  original author/commit metadata (the default for both `git cherry-pick` and
  `git merge` — don't rewrite authorship) so `git log`/`git blame` continue to
  credit the original author correctly.
- `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md` are upstream's own documents and
  should stay as they are; if the fork ever needs fork-specific contribution
  rules, add them here (`FORK_MAINTENANCE.md`) or in a new fork-specific file
  rather than editing upstream's.

## Quick reference

```bash
# One-time setup
git remote add upstream https://github.com/Casvt/Kapowarr.git

# Check what's new upstream
git fetch upstream
git log --oneline $(git merge-base origin/main upstream/main)..upstream/main

# Import one fix
git cherry-pick <sha>

# Import a broader catch-up
git checkout -b upstream-sync-$(date +%Y%m%d)
git merge upstream/main
# resolve conflicts per "Resolve conflicts" above, then:
mypy --explicit-package-bases . && isort --check-only . && \
  mkdir -p db && python -m unittest discover -s ./tests -p '*.py'
# open a PR into main like any other change
```
