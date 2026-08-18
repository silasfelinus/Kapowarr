# Acquisition quality provenance and safe upgrades

Kapowarr now has selection preferences for acquisition protocol, individual indexers, external download clients, GetComics HD/SD variants, and issue packs. Those preferences are safe for choosing a new download because the normal matcher still decides whether a release is valid first.

Replacing a file that is already in the library is a different problem. The current `files` record only preserves the file path and size. That is not enough evidence to decide that a newly found release is truly an upgrade.

## Safety rule

**Never automatically replace an existing library file unless Kapowarr can compare durable provenance for the existing file and the candidate and prove that the candidate is strictly better.**

An existing file with unknown provenance is therefore protected. A manual replacement can still be requested by the user, but an automatic upgrade search must leave it alone.

## Provenance that should be persisted

A future acquisition-provenance record should be keyed to the durable file ID rather than only the path, so normal renames do not erase identity. At minimum it should record:

- acquisition protocol: direct, torrent, or Usenet;
- source/indexer identity and configured source title where known;
- external download-client identity where applicable;
- source release URL or stable release identifier where available;
- GetComics quality label (`hd`, `sd`, or unknown) when explicitly present;
- whether the selected release was a single issue or a multi-issue pack;
- the original covered issue/range metadata;
- acquisition timestamp;
- whether the provenance is complete enough to participate in automatic upgrades.

It should not infer HD/SD, language, scan quality, or edition quality from file size alone.

## Upgrade comparison contract

A future automatic upgrade pass should:

1. run the existing match validation first;
2. refuse automatic replacement when the existing file lacks comparable provenance;
3. compare only preference dimensions that are known on both sides;
4. require a strictly better policy score, never merely a different source;
5. refuse a candidate that loses any known quality dimension unless the user has explicitly made that trade-off preferable;
6. stage/download the candidate before replacing the current file;
7. preserve the current file until the replacement has imported and matched successfully;
8. record the new provenance atomically with the successful file registration;
9. keep the old acquisition in history/blocklist context so a failed upgrade does not loop forever.

## Integration seam

The right place to create provenance is the successful post-processing/import path, after pack normalization and final file registration have established the actual file IDs. Conversion/rename operations must either preserve the provenance relation to the surviving file ID or deliberately transfer it to the replacement file.

This is intentionally a gate for automatic upgrades, not a blocker for current acquisition preferences. Until that persistence exists, Kapowarr should search only for missing monitored issues automatically and should not replace a known-good file merely because another source has a higher configured priority.
