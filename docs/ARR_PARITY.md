# Kapowarr ARR parity and roadmap

Kapowarr should feel immediately legible to someone coming from Sonarr or Radarr without copying features that do not make sense for comics. This document is the working parity audit **and** near-term roadmap for the current Kapowarr fork.

> Naming scope: this roadmap describes **Kapowarr**, the application being developed in this repository. `Comicarr` is reserved for a possible future separately branded app/interface and is not shorthand for the current fork.

## Current baseline — 2026-08-19

Recent merged milestones have materially changed the roadmap:

| PR | Capability | State now |
| --- | --- | --- |
| #69 | System Logs | Supported: in-app severity/search/filter, capture level, refresh/auto-refresh, raw download. |
| #70 | Backup / restore | Supported: manual + weekly SQLite-safe backups, retention and staged restore. |
| #71 | Continuous Library Import calibration | Supported / tuning: 30-second ComicVine operation pacing, local-sidecar fast path paced too, unique non-contradictory matches accepted, ties/contradictions held for review. |
| #72 | System Events | Supported: task/download/log timeline plus ComicVine provider-operation telemetry. |
| #73 | Import Lists | Supported / expanding: Remote ComicRack CBL provider with safe exact-ID automatic add and persistent sync. |
| #74 | File provenance | Supported: successful acquisitions persist source context on durable file IDs without secret-bearing acquisition URLs. |
| #75 | Explainable file quality | Supported foundation: evidence-backed format/source/GetComics traits, no fake aggregate score or automatic replacement. |
| #76 | ComicInfo import identity | Supported for sidecar ComicInfo and embedded CBZ/ZIP ComicInfo when `Web` contains an exact ComicVine volume URL; legacy `cvinfo.xml` is also consumed. |
| #78 | CBR/RAR ComicInfo intake | Supported: embedded ComicInfo is read through Kapowarr's bundled RAR tooling using isolated temporary extraction, without modifying source archives. |
| #79 | Portable series metadata export | Supported foundation: preview/download/create of Mylar-compatible `series.json`, preserving existing metadata by default and never inventing ComicVine identity. |

Metron is also a native metadata provider with provider-neutral identity storage and ComicVine-aware fallback paths. ComicVine remains the default authority where a ComicVine-linked identity is required.

## Default rule

When both Sonarr and Radarr expose a user-facing operational capability, treat it as an expected `*arr` convention. Kapowarr should either:

1. provide the equivalent capability using comic-appropriate behavior and naming; or
2. explicitly document why the capability does not fit Kapowarr.

Silence is not a decision. Missing expected features should become visible roadmap work instead of being rediscovered during troubleshooting.

## System parity

| Capability | Kapowarr status | Kapowarr interpretation |
| --- | --- | --- |
| Status / health | Supported | Existing health checks and runtime/about data are the right equivalent. |
| Tasks | Supported | Recurring work is visible; Backup and Import Lists use persistent task scheduling. |
| Logs | Supported | Capture granularity is separate from severity/search filtering and raw download remains available. |
| Backup / restore | Supported | Manual + weekly SQLite-safe backups, 28-day retention, authenticated management and staged restore with a pre-restore snapshot. |
| Events | Supported | Unified read-only timeline reuses task history, download history and recent warning/error logs. ComicVine provider-operation telemetry is explicitly not presented as raw HTTP packet accounting. |
| Updates | Partial / needs decision | Running version is visible, but there is no familiar current/latest/channel surface. Container-first installs should not gain a blind in-app self-updater. |

## Shared Settings parity

| Shared setting concept | Kapowarr status | Kapowarr interpretation |
| --- | --- | --- |
| Custom Formats | Partial | Protocol/source priority, GetComics HD/SD preference, pack preference, indexer/client priority and format preference cover some intent. A composable scoring/rule system does not yet exist. |
| Download Clients | Supported | Multiple torrent/Usenet clients and priority/load-balancing behavior are first-class. |
| General | Supported | Authentication, API key, hosting, proxy, UI title and logging controls are present. |
| Import Lists | Supported / expanding | Remote CBL is the first comic-native provider. Automatic Add deliberately requires exact embedded ComicVine volume identity; unresolved title-only entries do not trigger fuzzy metadata storms. |
| Indexers | Supported | Newznab and Torznab/Prowlarr/Jackett feeds support enable/disable, categories where explicit, and priority. |
| Media Management | Supported | Naming, root folders, deleted-issue behavior, file dates, chmod/chown, conversion/extraction and format preference are substantial. |
| Metadata | Partial / improving | ComicVine and native Metron identities coexist. Library Import consumes Mylar JSON/cvinfo/cvinfo.xml and standard ComicInfo beside files or embedded in CBZ/ZIP/CBR/RAR. Kapowarr can generate preservation-aware `series.json`; archive-level ComicInfo mutation remains deliberately deferred. |
| Notifications | Supported | Discord/generic webhook notifications exist and should grow only with useful event coverage. |
| Profiles | Missing / deferred | Do not clone video quality profiles literally. Add per-series acquisition profiles only if they materially reduce repetitive setup after quality policy is defined. |
| Quality | Partial / foundation complete | Per-file provenance and read-only quality explanations exist. Unknown evidence remains unknown; no aggregate score or automatic replacement policy exists yet. |

## Workflow parity audit

| Workflow family | Current Kapowarr read |
| --- | --- |
| Library + mass editing | Supported / partial: image-first library, mass edit and large-library hydration exist; continue auditing batch actions against mature `*arr` expectations. |
| Wanted / missing + manual search | Supported: global Wanted, bulk acquisition and manual search are present. |
| Calendar / release awareness | Supported / comic-shaped: weekly publisher-aware release catalogue and subscriptions are the current equivalent; a conventional date calendar is optional rather than assumed. |
| Search + acquisition | Supported / expanding: GetComics, Newznab, Torznab and client routing are first-class. Watched-folder import and additional lawful sources remain roadmap work. |
| Completed-download handling | Supported / partial: torrent/Usenet lifecycle, seed-safe import, failure handling and durable file provenance are substantial; watched-folder external import remains missing. |
| History + blocklist | Supported: acquisition history/blocklist stay focused while System Events provides the operational cross-stream timeline. |
| Portable metadata | Supported foundation / expanding: exact ComicInfo identity is consumed from sidecars and common ZIP/RAR comic containers; `series.json` can be previewed, downloaded or safely materialized with existing files preserved by default. Embedded archive metadata write-back is not yet enabled. |
| Reader | Intentionally secondary | Reader parity is not an `*arr` convention and should not displace aggregation priorities. |

## Near-term roadmap

Prioritize work that reduces metadata dependency, support friction or data-loss risk before ornamental parity:

1. **Portable metadata UI + archive policy** — add a minimal General Files affordance for preview/create of the safe `series.json` export, then define merge/preservation semantics before considering embedded ComicInfo mutation. Do not expose casual overwrite controls.
2. **Watched-folder / external completed-download import** — safely ingest files obtained outside Kapowarr and route them through the same matching, provenance and post-processing rules.
3. **ComicVine failure taxonomy and telemetry follow-up** — if real-world import tests still hit suspicious cooldowns, distinguish confirmed ComicVine rate-limit status from transport/JSON failures and expose endpoint/provider-operation evidence clearly.
4. **Profiles + Quality policy** — only after evidence is durable, decide whether known traits should be combined at all, define strictly-better comparisons and rollback/keep-old semantics, then consider per-series profiles.
5. **Updates UX** — expose understandable current/latest/channel state without unsafe self-update behavior for container installs.
6. **System / Settings navigation** — make Logs, Events, Backup and Import Lists familiar sibling destinations when the sidebar can be refactored without bloating the minimalist UI.
7. **Import List providers** — add additional curated/publisher/list ecosystems only when they expose stable identities or can be resolved without metadata-search storms.

## Quality / replacement safety gate

Automatic replacement remains intentionally disabled. Before Kapowarr can replace an existing library file unattended it must have:

- comparable durable evidence for the current file and candidate;
- an explicit strictly-better policy rather than a vague score;
- no implicit penalty for unknown evidence or reward for file size alone;
- staged replacement so the existing file survives until the candidate imports and matches successfully;
- provenance transfer/update that follows the actual surviving file;
- loop prevention and understandable rollback/history behavior.

## Ongoing audit process

For each major Sonarr/Radarr surface, compare the two mature `*arr` applications first, then compare Kapowarr. Record each item as `supported`, `partial`, `missing`, or `not-applicable`, with a short reason. Shared Sonarr+Radarr behavior is the baseline expectation, not an automatic implementation mandate.

## UX constraint

Parity should reduce relearning, not import every control from another application. Preserve Kapowarr's minimalist, image-first interface. Prefer familiar locations, terms, defaults, and workflows; hide advanced controls until they are useful rather than building a cockpit.
