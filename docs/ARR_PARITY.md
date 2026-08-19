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
| Events | Supported | Unified read-only timeline reuses task history, download history and recent warning/error logs instead of duplicating them in another database table. ComicVine provider-operation telemetry is process-local and explicitly distinguished from raw HTTP request counts. |
| Updates | Partial / needs decision | Running version is visible, but there is no Sonarr/Radarr-style update surface. Container-first installs should not gain a blind in-app updater; they still need understandable current/latest/update-channel state where safe. |

## Shared Settings parity

Current Sonarr and Radarr both expose settings areas for Custom Formats, Download Clients, General, Import Lists, Indexers, Media Management, Metadata, Notifications, Profiles, and Quality.

| Shared setting concept | Kapowarr status | Comicarr interpretation |
| --- | --- | --- |
| Custom Formats | Partial | Protocol/source priority, GetComics HD/SD preference, pack preference, indexer/client priority and format preference cover some intent. A composable scoring/rule system does not yet exist. |
| Download Clients | Supported | Multiple torrent/Usenet clients and priority/load-balancing behavior are already first-class. |
| General | Supported | Authentication, API key, hosting, proxy, UI title and logging controls are present. |
| Import Lists | Supported / expanding | Remote ComicRack CBL lists provide the first comic-native Import List provider with Enabled, Automatic Add, Root Folder, monitor/search-on-add behavior, global ComicVine exclusions, manual sync and persistent 12-hour sync. Automatic Add deliberately requires exact embedded ComicVine volume IDs; title-only CBL entries remain unresolved rather than triggering fuzzy searches. Add more useful list providers as real comic ecosystems justify them. |
| Indexers | Supported | Newznab and Torznab/Prowlarr/Jackett feeds support enable/disable, categories where explicit, and priority. |
| Media Management | Supported | Naming, root folders, deleted-issue behavior, file dates, chmod/chown, conversion/extraction and format preference are already substantial. |
| Metadata | Partial | ComicVine + native Metron provider identity is supported. Portable ComicInfo.xml / series metadata import-export remains a real gap. |
| Notifications | Supported | Discord/generic webhook notifications exist and should grow only with useful event coverage. |
| Profiles | Missing | Do not clone video quality profiles literally. Design a comic acquisition profile only if per-series bundles of format/source/upgrade rules materially reduce repetitive setup. |
| Quality | Partial | Global source/format preferences exist, and successful acquisitions now persist per-file source type/name, release context, acquisition time, and refreshed file size without retaining raw token-bearing download URLs. This creates trustworthy comparison evidence, but no comic-specific quality score or automatic replacement policy exists yet. |

## Workflow parity audit

The same comparison applies outside Settings. Track these as explicit `supported`, `partial`, `missing`, or `not-applicable` decisions:

| Workflow family | Current Kapowarr read |
| --- | --- |
| Library + mass editing | Supported / partial: image-first library, mass edit and large-library hydration exist; continue auditing batch actions against mature *arr expectations. |
| Wanted / missing + manual search | Supported: global Wanted, bulk acquisition and manual search are present. |
| Calendar / release awareness | Supported / comic-shaped: weekly publisher-aware release catalogue and subscriptions are the current equivalent; audit whether a conventional date calendar would add value. |
| Search + acquisition | Supported / expanding: GetComics, Newznab, Torznab and client routing are first-class; watched-folder import and additional lawful sources remain roadmap work. |
| Completed-download handling | Supported / partial: torrent/Usenet lifecycle, seed-safe import, failure handling and new durable file provenance are substantial; watched-folder external import remains missing. |
| History + blocklist | Supported: acquisition history/blocklist stay focused, while System Events provides the operational cross-stream timeline. |
| Portable metadata | Partial: provider-neutral IDs are durable, file-embedded metadata portability remains missing. |
| Reader | Intentionally secondary: reader parity is not an *arr convention and should not displace aggregation priorities. |

## Next parity audits

Prioritize shared expectations that reduce support friction or data-loss risk before ornamental parity:

1. **Profiles + Quality scoring** — with per-file provenance in place, define the small set of comic-specific signals worth scoring before deciding whether per-series acquisition profiles or automatic upgrades are justified.
2. **Updates** — define the expected UX for Docker/container installs versus native installs without teaching Kapowarr to self-update in unsafe environments.
3. **System navigation** — Logs, Events and Backup are visible from Status today; Import Lists is linked from Metadata settings. When the monolithic sidebar can be safely refactored, make shared System and Settings capabilities familiar sibling links without bloating the main navigation.
4. **Import List providers** — add additional curated/publisher/list ecosystems only when they expose stable identities or can be resolved without turning list sync into a metadata-search storm.

## Ongoing audit process

For each major Sonarr/Radarr surface, compare the two mature `*arr` applications first, then compare Kapowarr. Record each item as `supported`, `partial`, `missing`, or `not-applicable`, with a short reason. Shared Sonarr+Radarr behavior is the baseline expectation, not an automatic implementation mandate.

## UX constraint

Parity should reduce relearning, not import every control from another application. Preserve Kapowarr's minimalist, image-first interface. Prefer familiar locations, terms, defaults, and workflows; hide advanced controls until they are useful rather than building a cockpit.
