# Acquisition quality provenance and safe upgrades

Kapowarr has selection preferences for acquisition protocol, individual indexers, external download clients, GetComics HD/SD variants, issue packs and file format. Those preferences are safe for choosing a new download because the normal matcher still decides whether a release is valid first.

Replacing a file that is already in the library is a different problem. Kapowarr now persists acquisition provenance on the durable library `file_id`, but that evidence is intentionally narrower than a complete automatic-upgrade policy.

## Current implemented foundation

Successful direct, completed torrent/Usenet and seed-safe torrent-copy imports attach provenance to the actual surviving library file after conversion. Current durable evidence includes:

- acquisition/source type;
- source/indexer name where known;
- release/title context (`release_title`, `web_title`, `web_sub_title`);
- acquisition timestamp;
- the refreshed size of the surviving file in the existing `files` row.

The provenance relation follows the durable file ID, so a normal rename does not erase it. Replacing the same registered path updates the provenance record, and deleting the library file cascades the provenance away.

For security, Kapowarr deliberately does **not** persist raw download URLs, magnets, NZB URLs, `pure_link` values or other acquisition URLs that may contain API keys/tokens.

A read-only quality explanation layer can currently describe only evidence-backed traits:

- current file format and its position in `format_preference`;
- known direct/torrent/Usenet source and its position in the configured source preference;
- explicit GetComics HD/SD labeling when provenance proves the file actually came from a GetComics source.

Unknown evidence stays unknown. There is no aggregate quality score and no automatic replacement behavior.

## Safety rule

**Never automatically replace an existing library file unless Kapowarr can compare durable evidence for the existing file and the candidate and prove that the candidate is strictly better under an explicit policy.**

An existing file with unknown or incomparable provenance is therefore protected. Manual replacement may still be requested by the user, but an automatic upgrade search must leave that file alone.

## Evidence still missing for richer upgrade decisions

Future provenance may need additional comparison-safe fields, but only when a real policy needs them. Candidates include:

- external download-client identity where it materially affects policy;
- a safe stable release identifier that does not contain credentials;
- whether the selected acquisition was a single issue or multi-issue pack;
- original covered issue/range metadata;
- explicit language/edition/scan traits only when a trustworthy source supplies them.

Kapowarr must not infer HD/SD, language, scan quality or edition quality from file size alone.

## Upgrade comparison contract

A future automatic upgrade pass must:

1. run the existing match validation first;
2. refuse automatic replacement when current and candidate evidence is not comparable;
3. compare only dimensions that are known on both sides;
4. require a strictly better explicit policy outcome, never merely a different source;
5. refuse a candidate that loses any known dimension unless the user has explicitly made that trade-off preferable;
6. stage/download the candidate before replacing the current file;
7. preserve the current file until the replacement has imported and matched successfully;
8. update provenance only for the actual surviving replacement file;
9. keep enough history/blocklist context that a failed upgrade cannot loop forever.

## Current decision

Provenance persistence is no longer the blocker. The remaining blocker is **policy**: Kapowarr does not yet have enough reason to combine the known traits into a single score, and no rollback/keep-old contract exists for unattended replacement.

Until those rules are explicit, automatic searches should continue to target missing monitored content rather than replacing a known-good file simply because another source or format has a higher configured preference.
