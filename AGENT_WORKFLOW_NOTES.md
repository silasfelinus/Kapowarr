# Notes for AI agents working on this fork

This document is for the AI agents (conductor sessions, background subagents,
external Worker connectors) that implement, review, and merge changes to this
repository. It is not upstream documentation and not general fork-maintenance
guidance — see [`FORK_MAINTENANCE.md`](./FORK_MAINTENANCE.md) for tracking
upstream. It covers workflow hazards specific to how agents push code here.

## Content-API push workarounds silently drop concurrent changes

Normal work pushes with plain `git push`/`git rebase`, which gives a real
safety net: a non-fast-forward rejection when the remote has moved since you
last fetched. Some agent sandboxes can't do that — a background agent
isolated from the shared working directory, or a session hitting a git-proxy
limitation, may fall back to pushing via the GitHub content API instead
(`create_branch` + `push_files`/`create_or_update_file`). That workaround has
no equivalent safety net: there's no shared ref history for the API to reject
a stale write against, so it will happily overwrite a file with content built
from a start-of-task snapshot even if `main` has moved since.

This happened for real (kapowarr/t-027, 2026-08-18, `silasfelinus/Kapowarr#42`):
a background agent building the weekly-release/pull-list feature had its
sandbox correctly refuse `git push` against the shared checkout (working as
intended — see the isolation note below), so it built and tested against an
isolated copy and pushed the finished branch via `create_branch` + `push_files`.
That push carried each touched file's *full content* as constructed against
`main`'s state at task start. `main` had moved concurrently in the meantime —
`kapowarr/t-026` merged mid-task and added an `import frontend.torznab` line
to `frontend/ui.py`, a file the t-027 agent also touched. The agent's push
silently reverted that line, because it only diffed its own before/after
snapshot and never re-fetched `frontend/ui.py`'s *current* remote content
before constructing its write. Neither the agent's own tests (run against its
isolated copy, which by definition also lacked the concurrent change) nor the
PR's CI caught it — removing an import with no direct test coverage doesn't
fail an import-time test, it just makes the routes that import registers
silently stop existing. It was only caught because the reviewing session
pulled the PR's actual file-level diff before merging and noticed a removed
import line with no business being in a weekly-pull-list PR.

**If you are an agent about to use a content-API push workaround** (because
`git push` is unavailable, blocked by sandbox isolation, or hitting an
HTTP 413/proxy limitation — see the conductor repo's own `CLAUDE.md` for the
general pattern this belongs to):

- Re-fetch each file you're about to write via `get_file_contents` (or
  equivalent) at the target branch's **current** tip immediately before
  constructing the write — not the snapshot you started the task from. Build
  your edit as a diff against that live content, not by re-emitting a whole
  file you already had in memory.
- If a file you touch has changed on the base branch since you started,
  reconcile your edit against the new content rather than overwriting it.
- This applies per-file, not just once at task start: a long-running task
  that touches several files over time should re-check each one right before
  its own write, since other files may have moved even after you already
  re-checked the first one.

**If you are a reviewing session merging a PR that used this workaround**
(or any PR you're not certain used a normal `git push`):

- Don't trust green CI alone. Pull the PR's actual file-level diff
  (`pull_request_read` with `get_files`, or equivalent) and read it, not just
  the PR description's self-report of what changed.
- Look specifically for removed lines that have no relationship to the task's
  stated scope — an unexplained deletion in an unrelated file is the
  signature of this failure mode, and it's exactly the kind of change CI is
  least likely to catch (import-time-only code with no direct test).
- If you find one, fix it with a single follow-up write keyed off the file's
  *live* blob SHA on the PR branch (so your own fix can't repeat the same
  mistake), re-verify CI, and confirm the resulting diff is scoped to what
  the task actually intended before merging.

## Why the sandbox blocks `git push` from a shared checkout in the first place

This is intentional, not a bug to route around casually: a non-isolated
background agent running git-mutating commands in a working directory a
foreground session is still using can silently discard that session's
uncommitted edits or delete its branch outright. The content-API push
workaround exists for the cases where isolation is genuinely required and a
fresh worktree isn't available — it trades away git's own conflict-detection
safety net to get there, which is exactly why the re-fetch-before-write
discipline above matters. Prefer a fresh, isolated clone/worktree with normal
`git push` over the content-API workaround whenever one is available; reach
for the workaround only when it genuinely isn't.
