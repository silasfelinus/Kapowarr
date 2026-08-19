# Comicarr parity principle

Kapowarr should feel immediately legible to someone coming from Sonarr or Radarr without copying features that do not make sense for comics.

## Default rule

When both Sonarr and Radarr expose a user-facing operational capability, treat it as an expected `*arr` convention. Kapowarr should either:

1. provide the equivalent capability using comic-appropriate behavior and naming; or
2. explicitly document why the capability does not fit Kapowarr.

Silence is not a decision. Missing expected features should become visible roadmap work instead of being rediscovered during troubleshooting.

## System parity

Current Sonarr and Radarr both expose these first-class System capabilities:

| Capability | Kapowarr status | Comicarr interpretation |
| --- | --- | --- |
| Status / health | Supported | Existing health checks and runtime/about data are the right equivalent. |
| Tasks | Supported | Keep recurring work visible here. Database Backup now uses this same persistent interval-task machinery rather than a private timer. |
| Logs | Supported | In-app viewer separates capture granularity from severity/search filtering and retains raw download. |
| Backup / restore | Supported | Manual + weekly SQLite-safe backups, 28-day retention, authenticated list/download/delete, listed or uploaded restore, and automatic pre-restore snapshot. |
| Events | Partial | Download History and task history capture some events, but there is no unified operational event stream. Audit signal gaps before creating another history table. |
| Updates | Partial / needs decision | Running version is visible, but there is no Sonarr/Radarr-style update surface. Container-first installs should not gain a blind in-app updater; they still need understandable current/latest/update-channel state where safe. |

## Shared Settings parity

Current Sonarr and Radarr both expose settings areas for Custom Formats, Download Clients, General, Import Lists, Indexers, Media Management, Metadata, Notifications, Profiles, and Quality.

| Shared setting concept | Kapowarr status | Comicarr interpretation |
| --- | --- | --- |
| Custom Formats | Partial | Protocol/source priority, GetComics HD/SD preference, pack preference, indexer/client priority and format preference cover some intent. A composable scoring/rule system does not yet exist. |
| Download Clients | Supported | Multiple torrent/Usenet clients and priority/load-balancing behavior are already first-class. |
| General | Supported | Authentication, API key, hosting, proxy, UI title and logging controls are present. |
| Import Lists | Missing | Library Import is filesystem ingestion, not an *arr Import List. Evaluate list/feed-based automatic series discovery separately, especially for publisher/reading-list workflows. |
| Indexers | Supported | Newznab and Torznab/Prowlarr/Jackett feeds support enable/disable, categories where explicit, and priority. |
| Media Management | Supported | Naming, root folders, deleted-issue behavior, file dates, chmod/chown, conversion/extraction and format preference are already substantial. |
| Metadata | Partial | ComicVine + native Metron provider identity is supported. Portable ComicInfo.xml / series metadata import-export remains a real gap. |
| Notifications | Supported | Discord/generic webhook notifications exist and should grow only with useful event coverage. |
| Profiles | Missing | Do not clone video quality profiles literally. Design a comic acquisition profile only if per-series bundles of format/source/upgrade rules materially reduce repetitive setup. |
| Quality | Partial | Global source/format preferences exist. Durable per-file acquisition provenance and safe automatic upgrades are still missing, so a known-good file cannot yet be compared/replaced with Sonarr/Radarr confidence. |

## Workflow parity audit

The same comparison applies outside Settings. Track these as explicit `supported`, `partial`, `missing`, or `not-applicable` decisions:

| Workflow family | Current Kapowarr read |
| --- | --- |
| Library + mass editing | Supported / partial: image-first library, mass edit and large-library hydration exist; continue auditing batch actions against mature *arr expectations. |
| Wanted / missing + manual search | Supported: global Wanted, bulk acquisition and manual search are present. |
| Calendar / release awareness | Supported / comic-shaped: weekly publisher-aware release catalogue and subscriptions are the current equivalent; audit whether a conventional date calendar would add value. |
| Search + acquisition | Supported / expanding: GetComics, Newznab, Torznab and client routing are first-class; watched-folder import and additional lawful sources remain roadmap work. |
| Completed-download handling | Supported / partial: torrent/Usenet lifecycle, seed-safe import and failure handling are substantial; watched-folder external import remains missing. |
| History + blocklist | Supported for acquisition; operational Events remains a separate audit. |
| Portable metadata | Partial: provider-neutral IDs are durable, file-embedded metadata portability remains missing. |
| Reader | Intentionally secondary: reader parity is not an *arr convention and should not displace aggregation priorities. |

## Next parity audits

Prioritize shared expectations that reduce support friction or data-loss risk before ornamental parity:

1. **System Events** — map what Download History, task history, health and logs already capture; add only the missing operational event signal.
2. **Import Lists** — determine the comic-native equivalent for automatically following curated/publisher/reading lists without confusing it with filesystem Library Import.
3. **Profiles + Quality** — decide whether per-series acquisition profiles are justified, and finish durable file provenance before any automatic replacement upgrades.
4. **Updates** — define the expected UX for Docker/container installs versus native installs without teaching Kapowarr to self-update in unsafe environments.
5. **System navigation** — Logs and Backup are visible from Status today; when the monolithic sidebar is safely refactored, make shared System capabilities sibling links like other *arrs without bloating the main navigation.

## Ongoing audit process

For each major Sonarr/Radarr surface, compare the two mature `*arr` applications first, then compare Kapowarr. Record each item as `supported`, `partial`, `missing`, or `not-applicable`, with a short reason. Shared Sonarr+Radarr behavior is the baseline expectation, not an automatic implementation mandate.

## UX constraint

Parity should reduce relearning, not import every control from another application. Preserve Kapowarr's minimalist, image-first interface. Prefer familiar locations, terms, defaults, and workflows; hide advanced controls until they are useful rather than building a cockpit.
