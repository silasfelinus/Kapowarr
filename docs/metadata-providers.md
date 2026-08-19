# Metadata providers

Kapowarr treats metadata identity separately from download sources. ComicVine
remains the built-in default, but consumers obtain it through the metadata
provider registry rather than constructing a global ComicVine client directly.

Each provider declares a stable lowercase `provider_id` and explicit
capabilities. Provider responses retain the legacy `comicvine_id` fields while
the existing ComicVine UI/API is supported, and also carry normalized
`provider_id` / `external_id` identity plus cover provenance. A future provider
can therefore be added without changing the registry contract or pretending its
IDs belong to ComicVine.

## Stored identity and conflicts

`volume_external_ids` and `issue_external_ids` are additive identity maps:

- one local entity may have one external ID from each provider;
- one `(provider_id, external_id)` may resolve to only one local entity;
- adding another provider enriches an entity and never replaces its existing
  identity;
- a conflicting provider ID raises a database constraint error instead of
  silently merging two volumes or issues;
- existing `comicvine_id` values are backfilled and retained for compatibility.

Cover bytes remain in `volumes_covers`. The provider, external ID and source URL
stored beside them record where the current image came from, so refresh logic can
make an explicit replacement decision instead of confusing cover provenance
with series identity.

## Provider implementation contract

Providers subclass `MetadataProvider`, register lazily with
`MetadataProviderRegistry`, and normalize volume search, volume fetch, bulk
volume fetch and issue fetch results. Callers should use
`get_metadata_provider(provider_id)` and check declared capabilities rather than
importing a concrete provider.

Provider credentials and enable/fallback policy are intentionally not stored in
the identity tables. Those are runtime configuration concerns for the concrete
Metron integration; changing them must not orphan existing library records.
