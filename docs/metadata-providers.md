# Metadata providers

Kapowarr treats metadata identity separately from download sources. ComicVine remains the built-in default authority for the existing library model, while Metron is a native second provider. Consumers obtain providers through the metadata registry rather than constructing global provider clients directly.

Each provider declares a stable lowercase `provider_id` and explicit capabilities. Provider responses retain legacy `comicvine_id` fields where the existing ComicVine UI/API contract still requires them, and also carry normalized `provider_id` / `external_id` identity plus cover provenance. New providers can therefore be added without pretending their IDs belong to ComicVine.

## Current built-in providers

### ComicVine

ComicVine remains the default provider and the compatibility anchor for volumes that already carry ComicVine IDs. Its provider operations are instrumented for System Events so Kapowarr can distinguish search/fetch activity and operation outcomes without presenting those counters as raw HTTP request counts.

### Metron

Metron is implemented as a native metadata provider and can be configured with an API token or username/password credentials.

When Metron is configured:

- metadata search can query ComicVine and Metron while retaining explicit provider identity on each result;
- if ComicVine volume fetches fail because of a ComicVine rate-limit or invalid-key condition, Kapowarr can resolve the ComicVine-linked volume through Metron's ComicVine cross-reference;
- bulk ComicVine volume fetches can be supplemented from Metron when ComicVine omits requested IDs;
- Metron identities are persisted beside, not in place of, existing ComicVine identity.

This is fallback/enrichment, not a silent ID conversion layer. A Metron external ID remains a Metron external ID.

## Stored identity and conflicts

`volume_external_ids` and `issue_external_ids` are additive identity maps:

- one local entity may have one external ID from each provider;
- one `(provider_id, external_id)` may resolve to only one local entity;
- adding another provider enriches an entity and never replaces its existing identity;
- a conflicting provider ID raises a database constraint error instead of silently merging two volumes or issues;
- existing `comicvine_id` values are backfilled and retained for compatibility.

Cover bytes remain in `volumes_covers`. Provider, external ID and source URL recorded beside the cover identify where the current image came from, so cover provenance does not get confused with series identity.

## Provider implementation contract

Providers subclass `MetadataProvider`, register lazily with `MetadataProviderRegistry`, and normalize volume search, volume fetch, bulk volume fetch and issue fetch results. Callers should use `get_metadata_provider(provider_id)` and declared capabilities rather than importing a concrete provider directly.

Provider credentials and enable/fallback policy are intentionally not stored in the identity tables. Those are runtime configuration concerns; changing or disabling a provider must not orphan library records that already carry its identity.

## Portable metadata

Provider-neutral database identity is durable, and Library Import can consume exact ComicVine identity from Mylar sidecars and standard ComicInfo `Web` URLs beside files or embedded in CBZ/ZIP/CBR/RAR archives.

Kapowarr can also generate a conservative Mylar-compatible `series.json` (schema version `1.0.2`) from a managed volume. The export fills only fields Kapowarr actually knows. A ComicVine `comicid` is written only when the volume truly has ComicVine identity; Metron IDs are never disguised as ComicVine IDs. Unknown lifecycle, imprint, age-rating, collection and publication-run data remain unknown rather than being inferred.

Portable series metadata is available through authenticated API endpoints:

- `GET /api/volumes/<id>/portable-metadata/series` previews the generated payload and reports whether a `series.json` already exists;
- `GET /api/volumes/<id>/portable-metadata/series/download` downloads the generated payload without modifying the library;
- `POST /api/volumes/<id>/portable-metadata/series` materializes `series.json` in the volume folder.

Write-back is preservation-first. POST defaults to `overwrite: false`; an existing `series.json` is returned as `existing_preserved` and is not touched. Explicit overwrite uses a same-folder temporary file followed by atomic replacement. Creating a new file uses exclusive creation so a concurrently-created third-party file cannot be clobbered. A successful write is passed through Kapowarr's normal file scanner so it becomes ordinary volume metadata in the library database.

Archive-level ComicInfo write-back remains intentionally separate. It should not begin until preservation/merge semantics for existing embedded metadata are explicit.
