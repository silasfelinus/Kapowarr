# Import Lists

Import Lists let Kapowarr follow an external list over time and, when explicitly enabled, add newly listed comic volumes to the library automatically.

This is different from two other Kapowarr features:

- **Library Import** scans comic files that already exist on disk and tries to match them to metadata.
- **Reading Lists** store an ordered set of issues for reading/story-arc purposes. A CBL Reading List can contain unresolved entries and can be used to search for missing issues.
- **Import Lists** periodically re-fetch an external source and can add newly referenced *volumes* to the Kapowarr library.

## Remote CBL

The first Import List provider is **Remote CBL**, using the ComicRack `.cbl` reading-list format that Kapowarr already supports for Reading Lists.

Configure a Remote CBL Import List with:

- **Name** — a friendly name for the source.
- **URL** — an `http://` or `https://` URL returning the CBL document.
- **Root Folder** — where automatically added volumes belong.
- **Enabled** — include this list in scheduled syncs.
- **Automatic Add** — allow exact volume identities from this list to be added to the library. This is off by default.
- **Monitor added volumes** — monitor volumes created by the list.
- **Monitor new issues** — monitor issues that appear later for those volumes.
- **Search on add** — optionally search for missing files after adding a volume.

## Exact identity, not fuzzy guessing

Automatic Add deliberately requires an **exact ComicVine volume ID embedded in the CBL**. Duplicate volume IDs are collapsed before processing.

A title-only CBL entry is still counted as an unresolved list item, but Kapowarr will not turn the title into a fuzzy ComicVine search and silently add a guessed volume. Import Lists are long-running unattended automation, so they use a stricter identity rule than filesystem Library Import.

Preview-only syncing is therefore useful even before enabling Automatic Add: the list status shows how many entries were found, how many unique exact volume identities were available, and how many entries remain unresolved.

## Existing volumes and exclusions

Kapowarr skips a listed volume when it is already in the library. Import List **Exclusions** apply globally by ComicVine volume ID, so a volume intentionally excluded from one automated source will not be re-added by another list.

Removing an item from a remote list does **not** remove a volume or delete files from Kapowarr. Import Lists are additive in this first implementation.

## Syncing and ComicVine pacing

Use **Sync** on one list or **Sync All** for an immediate refresh. Once an Import List has been configured, Kapowarr also enrolls Import List Sync in the normal persistent task scheduler on a 12-hour interval.

When Automatic Add needs ComicVine metadata, Kapowarr spaces new volume-add metadata starts by 30 seconds and shares that pacing clock across lists in the same sync run. This prevents multiple lists from creating a burst of add-time metadata requests.

The CBL document itself is fetched through Kapowarr's normal web-request session, which can use FlareSolverr for supported Cloudflare-protected web flows. ComicVine metadata API traffic remains separate from FlareSolverr.

## Current scope

Remote CBL is the first provider, not the final provider catalogue. Additional curated, publisher, or reading-list ecosystems should be added when they provide stable identities or can be resolved without turning a periodic list sync into a metadata-search storm.
