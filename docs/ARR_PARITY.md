# Comicarr parity principle

Kapowarr should feel immediately legible to someone coming from Sonarr or Radarr without copying features that do not make sense for comics.

## Default rule

When both Sonarr and Radarr expose a user-facing operational capability, treat it as an expected `*arr` convention. Kapowarr should either:

1. provide the equivalent capability using comic-appropriate behavior and naming; or
2. explicitly document why the capability does not fit Kapowarr.

Silence is not a decision. Missing expected features should become visible roadmap work instead of being rediscovered during troubleshooting.

## First audited System surface

Current Sonarr and Radarr both expose these first-class System capabilities:

| Capability | Kapowarr status | Action |
| --- | --- | --- |
| Status / health | Supported | Keep aligned where useful. |
| Tasks | Supported | Keep aligned where useful. |
| Logs | Supported by the log engine; viewer added in this parity pass | Keep capture level and viewer filtering distinct. |
| Backup / restore | Missing | Priority follow-up: scheduled/manual database + config backup, listing/download, retention, and safe restore workflow. |
| Events | Missing as a dedicated System surface | Audit whether Kapowarr history/task records already cover enough; add an operational event view if not. |
| Updates | No Sonarr/Radarr-style System surface | Evaluate against Kapowarr's container-first distribution. Do not add an in-app updater blindly; at minimum make running/latest version state understandable when it can be determined safely. |

## Ongoing audit process

Parity review is not limited to System. For each major Sonarr/Radarr surface, compare the two mature `*arr` applications first, then compare Kapowarr:

- Library and mass-editor workflows
- Wanted / missing and manual search
- Calendar / release awareness
- Quality and release preferences
- Indexers and download clients
- Import, completed-download handling, remote paths, and failed-download handling
- Notifications / connections
- Media management, naming, permissions, and recycling
- General settings, authentication, proxy, and API access
- System health, tasks, logs, events, backup/restore, and updates

Record each item as `supported`, `partial`, `missing`, or `not-applicable`, with a short reason. Shared Sonarr+Radarr behavior is the baseline expectation, not an automatic implementation mandate.

## UX constraint

Parity should reduce relearning, not import every control from another application. Preserve Kapowarr's minimalist, image-first interface. Prefer familiar locations, terms, defaults, and workflows; hide advanced controls until they are useful rather than building a cockpit.
