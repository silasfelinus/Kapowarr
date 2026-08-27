#!/usr/bin/env python3
"""sweep_loose_images.py — remove redundant page images from a comic library.

A library that stores comics as archives still accumulates loose page images:
`Moon Knight 26 (2009).jpg` and `Moon Knight 26 (2009)_thumb.jpg` sitting
beside the `.cbz` they came out of, left behind by extractors and readers.
Kapowarr counts `.jpg` as content (`FileConstants.CONTENT_EXTENSIONS`), so
those files are untracked content forever, and the folder holding them comes
back on every Rescan Untracked Library no matter how many times it imports.
In the 2026-08-27 log they were 697 of the 2927 files the scan declined.

Nothing in Kapowarr produces or reads `_thumb` files -- the name appears
nowhere in the codebase.

WHAT IT WILL NOT TOUCH
----------------------
- A folder with no comic archive in it. Kapowarr supports comics stored as
  loose page images (`filter_library_import_files`: "preserving real
  page-image comics"), and such a folder is indistinguishable from a pile of
  junk by filename alone. Only images sitting *beside an archive* are
  redundant by construction, and only those are ever candidates.
- Cover artwork. `cover.jpg`, `folder.jpg` and anything else Kapowarr reads
  as `SpecialVersion.COVER` is library decoration it already skips, not part
  of the problem this solves.
- Anything under a dot-directory (`.yacreaderlibrary/` and friends).
- Any file Kapowarr has a database row for. Deleting a tracked file breaks
  the volume that owns it, so `--db` is required before anything is removed;
  `--no-db-check` exists but says what it is.

SAFETY LADDER
-------------
Nothing happens without `--apply`. With it, files are *moved* to a
quarantine directory by default, keeping the tree shape so a mistake can be
poured back. `--delete` unlinks for real and has to be asked for by name.
Every run writes a CSV manifest of exactly what it considered.

Usage:
  # look only -- this is the default, and it changes nothing
  python scripts/sweep_loose_images.py /content --db /path/to/db.db

  # quarantine what it found, reversibly
  python scripts/sweep_loose_images.py /content --db /path/to/db.db \
      --apply --quarantine /content/.sweep-quarantine

  # and once you have looked in the quarantine folder and agree
  python scripts/sweep_loose_images.py /content --db /path/to/db.db \
      --apply --delete

Run it where the library is mounted -- inside the Kapowarr container the
root is `/content`, not the host path.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sqlite3
import sys
from typing import Dict, List, Set, Tuple

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
)

# Kapowarr's own definitions, not a private copy: what counts as an image,
# an archive or cover art has to be whatever the app thinks it is, or the
# sweep and the scan disagree about the same file.
from backend.base.definitions import (  # noqa: E402
    FileConstants, SpecialVersion)
from backend.base.file_extraction import extract_filename_data  # noqa: E402


IMAGE_EXTENSIONS = tuple(
    e.lower() for e in FileConstants.IMAGE_EXTENSIONS
)
ARCHIVE_EXTENSIONS = tuple(
    e.lower() for e in FileConstants.CONTAINER_EXTENSIONS
)


def is_cover_art(filepath: str) -> bool:
    """Whether Kapowarr reads this image as cover decoration.

    Deliberately the app's own answer rather than a filename list of our
    own: `is_library_import_artifact` skips exactly these already, so they
    are not what keeps a folder untracked.
    """
    try:
        return extract_filename_data(
            filepath, prefer_folder_year=True
        )['special_version'] == SpecialVersion.COVER
    except Exception:
        # An unreadable name is not a reason to delete something.
        return True


def classify(root: str) -> Tuple[List[str], Dict[str, int]]:
    """Walk `root` and return the removable images, plus a tally of why not.

    Only images in a directory that also holds a comic archive are
    removable: those are redundant by construction. A directory of images
    with no archive is a page-image comic as far as anything here can tell,
    and is left whole.
    """
    candidates: List[str] = []
    skipped: Dict[str, int] = {
        'folder has no archive (page-image comic)': 0,
        'cover art': 0,
        'inside a dot-directory': 0,
    }

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune dot-directories rather than walk and discard them.
        pruned = [d for d in dirnames if d.startswith('.')]
        for d in pruned:
            dirnames.remove(d)
        if pruned:
            skipped['inside a dot-directory'] += len(pruned)

        images = [
            f for f in filenames
            if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS
        ]
        if not images:
            continue

        has_archive = any(
            os.path.splitext(f)[1].lower() in ARCHIVE_EXTENSIONS
            for f in filenames
        )
        if not has_archive:
            skipped['folder has no archive (page-image comic)'] += len(images)
            continue

        for name in images:
            full = os.path.join(dirpath, name)
            if is_cover_art(full):
                skipped['cover art'] += 1
                continue
            candidates.append(full)

    return candidates, skipped


def tracked_filepaths(db_path: str) -> Set[str]:
    """Every filepath Kapowarr has a row for."""
    connection = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        return {
            row[0] for row in connection.execute('SELECT filepath FROM files;')
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('root', help='Library root, e.g. /content')
    parser.add_argument(
        '--db',
        help="Kapowarr's SQLite database. Required before anything is "
             "removed, so a file the library owns is never deleted."
    )
    parser.add_argument(
        '--no-db-check', action='store_true',
        help='Remove without checking the database first. Do not.'
    )
    parser.add_argument(
        '--thumbs-only', action='store_true',
        help='Consider only `*_thumb.*`, leaving other loose images alone.'
    )
    parser.add_argument(
        '--apply', action='store_true',
        help='Actually move or delete. Without it nothing changes.'
    )
    parser.add_argument(
        '--quarantine',
        help='Move files here instead of deleting them (default with '
             '--apply). The tree shape under `root` is preserved.'
    )
    parser.add_argument(
        '--delete', action='store_true',
        help='Unlink permanently instead of quarantining. No undo.'
    )
    parser.add_argument(
        '--manifest', default='sweep_loose_images.csv',
        help='Where to write the CSV of what was considered.'
    )
    args = parser.parse_args()

    if not os.path.isdir(args.root):
        print(f'error: {args.root} is not a directory', file=sys.stderr)
        return 2

    if args.apply and not (args.db or args.no_db_check):
        print(
            'error: --apply needs --db so a tracked file is never removed.\n'
            '       Pass --no-db-check only if you know the library is empty.',
            file=sys.stderr
        )
        return 2

    if args.apply and not args.delete and not args.quarantine:
        print(
            'error: --apply needs either --quarantine DIR (reversible) or\n'
            '       --delete (permanent).',
            file=sys.stderr
        )
        return 2

    candidates, skipped = classify(args.root)

    if args.thumbs_only:
        before = len(candidates)
        candidates = [
            c for c in candidates
            if '_thumb' in os.path.basename(c).lower()
        ]
        skipped['not a _thumb file (--thumbs-only)'] = before - len(candidates)

    tracked: Set[str] = set()
    if args.db:
        tracked = tracked_filepaths(args.db)
        before = len(candidates)
        candidates = [c for c in candidates if c not in tracked]
        skipped['tracked by Kapowarr'] = before - len(candidates)

    thumbs = sum(
        1 for c in candidates if '_thumb' in os.path.basename(c).lower()
    )
    total_bytes = 0
    for c in candidates:
        try:
            total_bytes += os.path.getsize(c)
        except OSError:
            pass

    print(f'Library root : {args.root}')
    print(f'Database     : {args.db or "NOT CHECKED"}')
    print()
    print(f'Removable    : {len(candidates)} files '
          f'({thumbs} of them _thumb), {total_bytes / 1e6:.1f} MB')
    print(f'Folders       : '
          f'{len({os.path.dirname(c) for c in candidates})}')
    print()
    print('Left alone:')
    for reason, count in sorted(skipped.items()):
        if count:
            print(f'  {count:6}  {reason}')

    with open(args.manifest, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['filepath', 'bytes', 'is_thumb'])
        for c in candidates:
            try:
                size = os.path.getsize(c)
            except OSError:
                size = -1
            writer.writerow(
                [c, size, '_thumb' in os.path.basename(c).lower()]
            )
    print()
    print(f'Manifest written to {args.manifest}')

    if not args.apply:
        print()
        print('Nothing was changed. Re-run with --apply to act on this.')
        return 0

    moved = removed = failed = 0
    for filepath in candidates:
        try:
            if args.delete:
                os.remove(filepath)
                removed += 1
            else:
                relative = os.path.relpath(filepath, args.root)
                destination = os.path.join(args.quarantine, relative)
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.move(filepath, destination)
                moved += 1
        except OSError as error:
            print(f'  could not handle {filepath}: {error}', file=sys.stderr)
            failed += 1

    print()
    if args.delete:
        print(f'Deleted {removed} file(s).')
    else:
        print(f'Moved {moved} file(s) to {args.quarantine}.')
        print('Check it, then delete that directory yourself when happy.')
    if failed:
        print(f'{failed} file(s) could not be handled; see above.')

    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
