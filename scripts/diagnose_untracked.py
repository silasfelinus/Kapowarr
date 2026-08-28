#!/usr/bin/env python3
"""diagnose_untracked.py — say why each folder keeps coming back.

Rescan Untracked Library rebuilds its pass from one signal: a folder holds
a content file with no row in `files`. That signal says a folder is stuck
and nothing about why, and the reasons are not alike -- some are the
library working correctly, some are a comic that will never import until
something changes.

This reads. It writes nothing, opens the database read-only, and touches
no file in the library.

Every verdict comes from Kapowarr's own predicates rather than a private
reimplementation, so what this calls cover art, a reader's cache or a
mismatch is what the importer calls it.

VERDICTS
--------
  reader-cache      Another application's thumbnails. Ignored since #155;
                    listed only so the count is visible.
  cover-art         Library decoration. Never was content.
  page-image-comic  A folder of loose images with no archive. Kapowarr
                    supports these; they are comics, not leftovers.
  loose-page-image  Images beside the archive they came out of. Redundant
                    -- `scripts/sweep_loose_images.py` removes these.
  empty-folder      No content at all. Since #156 these are searched by
                    name and held for review rather than ignored.
  no-volume-owns    Nothing in the library claims this folder, so the file
                    was never offered to a volume. An import job, not a
                    matching failure.
  volume-refused    A volume owns the folder and declined the file. This
                    is the interesting one, and it splits in two:
                      * WRONG-VOLUME  the file names a different series
                        than the volume, so refusing it is correct and
                        the folder is misfiled. Judged with the near-title
                        rule, so a parsed series carrying a leftover issue
                        number is not mistaken for a different comic.
                      * SAME-SERIES   the file names the volume's own
                        series and was still refused. That is a matching
                        bug, and this is how you find them.

Usage:
  python scripts/diagnose_untracked.py --db /path/to/db.db
  python scripts/diagnose_untracked.py --db /path/to/db.db \
      --verdict volume-refused
  python scripts/diagnose_untracked.py --db /path/to/db.db --csv report.csv

Run it where the library is mounted -- inside the Kapowarr container the
root is `/content`, not a host path.
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
)

from backend.base.definitions import (  # noqa: E402
    FileConstants, SpecialVersion)
from backend.base.file_extraction import (  # noqa: E402
    extract_filename_data, is_reader_cache_file)
from backend.base.helpers import extract_year_from_date  # noqa: E402
from backend.implementations.matching import (  # noqa: E402
    file_importing_filter, match_special_version, match_title,
    match_title_nearly, match_volume_number, match_year)


IMAGES = tuple(e.lower() for e in FileConstants.IMAGE_EXTENSIONS)
ARCHIVES = tuple(e.lower() for e in FileConstants.CONTAINER_EXTENSIONS)
CONTENT = tuple(e.lower() for e in FileConstants.CONTENT_EXTENSIONS)


def read_library(db_path: str) -> Tuple[set, List[Dict[str, Any]], List[str]]:
    """Everything this needs from the database, read-only."""
    connection = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tracked = {
            row['filepath']
            for row in connection.execute('SELECT filepath FROM files;')
        }
        roots = [
            row['folder']
            for row in connection.execute('SELECT folder FROM root_folders;')
        ]
        issues = defaultdict(list)
        for row in connection.execute(
            'SELECT volume_id, calculated_issue_number, date, title '
            'FROM issues;'
        ):
            issues[row['volume_id']].append(SimpleNamespace(
                id=0,
                calculated_issue_number=row['calculated_issue_number'],
                date=row['date'],
                title=row['title']
            ))
        volumes = []
        for row in connection.execute(
            'SELECT id, title, year, volume_number, folder, '
            'special_version, special_version_locked '
            'FROM volumes WHERE folder IS NOT NULL AND folder != "";'
        ):
            volumes.append({
                'id': row['id'],
                'folder': os.path.abspath(row['folder']),
                'data': SimpleNamespace(
                    title=row['title'],
                    year=row['year'],
                    volume_number=row['volume_number'],
                    special_version=SpecialVersion(row['special_version']),
                    # Read, not assumed: an inferred single-issue
                    # classification no longer refuses its own series'
                    # files while a locked one still does, so guessing
                    # this would make the verdicts disagree with the
                    # importer on exactly the volumes in question.
                    special_version_locked=bool(
                        row['special_version_locked']
                    ),
                    folder=row['folder']
                ),
                'issues': sorted(
                    issues.get(row['id'], []),
                    key=lambda i: i.calculated_issue_number
                )
            })
        return tracked, volumes, roots
    finally:
        connection.close()


def owning_volume(
    filepath: str, volumes: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """The volume whose folder contains this file, innermost first."""
    best = None
    for volume in volumes:
        folder = volume['folder']
        if filepath.startswith(folder + os.sep):
            if best is None or len(folder) > len(best['folder']):
                best = volume
    return best


def why_refused(
    file_data: Dict[str, Any], volume: Dict[str, Any]
) -> Tuple[str, str]:
    """Re-run the volume's own filter and name the gate that said no."""
    volume_data = volume['data']
    issues = volume['issues']
    number_to_year = {
        i.calculated_issue_number: extract_year_from_date(i.date)
        for i in issues
    }

    # The near rule as well as the strict one. A parser that leaves the
    # issue number in the series -- "Hell Her Way 001", "Flesh Eating
    # Cheerleaders Spring Break 001" -- makes strict equality call a file
    # a different series from the volume it plainly belongs to, and this
    # verdict exists precisely to tell those two apart.
    series = str(file_data['series'] or '')
    same_series = (
        match_title(series, volume_data.title)
        or match_title_nearly(series, volume_data.title)
    )

    issue_number = file_data['issue_number']
    if issue_number is None:
        issue_number = float('-inf')
    end_year = number_to_year.get(
        issue_number if isinstance(issue_number, float) else -1
    )

    gates = []
    if not match_special_version(
        volume_data.special_version, file_data['special_version'],
        volume_data.title, file_data['issue_number']
    ):
        gates.append('special-version')
    if not match_volume_number(
        volume_data, issues, file_data['volume_number']
    ):
        gates.append('volume-number')
    if not match_year(volume_data.year, file_data['year'], end_year):
        gates.append('year')

    kind = 'SAME-SERIES' if same_series else 'WRONG-VOLUME'
    return kind, '+'.join(gates) or 'unknown'


def diagnose(root: str, tracked: set, volumes: List[Dict[str, Any]]):
    rows = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]

        content = [
            f for f in filenames
            if os.path.splitext(f)[1].lower() in CONTENT
        ]
        untracked = [
            f for f in content
            if os.path.join(dirpath, f) not in tracked
        ]
        if not content and not dirnames:
            rows.append({
                'folder': dirpath, 'file': '', 'verdict': 'empty-folder',
                'detail': '', 'volume': ''
            })
            continue
        if not untracked:
            continue

        has_archive = any(
            os.path.splitext(f)[1].lower() in ARCHIVES for f in content
        )
        for name in untracked:
            full = os.path.join(dirpath, name)
            extension = os.path.splitext(name)[1].lower()
            file_data = extract_filename_data(full, prefer_folder_year=True)

            if is_reader_cache_file(full):
                verdict, detail = 'reader-cache', ''
            elif (
                extension in IMAGES
                and file_data['special_version'] == SpecialVersion.COVER
            ):
                verdict, detail = 'cover-art', ''
            elif extension in IMAGES and not has_archive:
                verdict, detail = 'page-image-comic', ''
            elif extension in IMAGES:
                verdict, detail = 'loose-page-image', ''
            else:
                volume = owning_volume(full, volumes)
                if volume is None:
                    verdict, detail = 'no-volume-owns', ''
                elif file_importing_filter(
                    file_data, volume['data'], volume['issues'],
                    {
                        i.calculated_issue_number: extract_year_from_date(
                            i.date
                        )
                        for i in volume['issues']
                    }
                ):
                    verdict, detail = 'accepted-but-unrecorded', ''
                else:
                    kind, gates = why_refused(file_data, volume)
                    verdict = 'volume-refused'
                    detail = f'{kind} ({gates})'

            rows.append({
                'folder': dirpath,
                'file': name,
                'verdict': verdict,
                'detail': detail,
                'volume': (
                    f"{volume['data'].title} ({volume['data'].year})"
                    if verdict in ('volume-refused', 'accepted-but-unrecorded')
                    else ''
                )
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--db', required=True, help="Kapowarr's database")
    parser.add_argument(
        '--root', action='append',
        help='Library root. Defaults to every root in the database.'
    )
    parser.add_argument('--verdict', help='Show only this verdict.')
    parser.add_argument('--csv', help='Write every row to this CSV.')
    parser.add_argument(
        '--limit', type=int, default=15,
        help='Examples printed per verdict (default 15).'
    )
    args = parser.parse_args()

    tracked, volumes, db_roots = read_library(args.db)
    roots = args.root or db_roots
    if not roots:
        print('error: no root folders; pass --root', file=sys.stderr)
        return 2

    rows = []
    for root in roots:
        if not os.path.isdir(root):
            print(f'warning: {root} is not readable here', file=sys.stderr)
            continue
        rows.extend(diagnose(root, tracked, volumes))

    if not rows:
        print('Nothing untracked. The library and the database agree.')
        return 0

    counts = Counter(r['verdict'] for r in rows)
    folders = {
        verdict: len({r['folder'] for r in rows if r['verdict'] == verdict})
        for verdict in counts
    }

    print(f'{len(tracked)} tracked files · {len(volumes)} volumes with a '
          f'folder\n')
    print(f'{"verdict":26} {"files":>7} {"folders":>8}')
    print('-' * 43)
    for verdict, count in counts.most_common():
        print(f'{verdict:26} {count:>7} {folders[verdict]:>8}')

    shown = [r for r in rows if not args.verdict
             or r['verdict'] == args.verdict]
    for verdict in sorted({r['verdict'] for r in shown}):
        if verdict in ('reader-cache', 'cover-art', 'page-image-comic'):
            continue
        examples = [r for r in shown if r['verdict'] == verdict]
        print(f'\n=== {verdict} ({len(examples)}) ===')
        detail_counts = Counter(r['detail'] for r in examples if r['detail'])
        for detail, count in detail_counts.most_common():
            print(f'  {count:>6}  {detail}')
        for row in examples[:args.limit]:
            print(f'    {row["file"] or os.path.basename(row["folder"])}')
            if row['volume']:
                print(f'        against volume {row["volume"]}  '
                      f'{row["detail"]}')
            print(f'        {row["folder"]}')
        if len(examples) > args.limit:
            print(f'    ... and {len(examples) - args.limit} more'
                  f' (use --csv for all)')

    if args.csv:
        with open(args.csv, 'w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=['folder', 'file', 'verdict', 'detail', 'volume']
            )
            writer.writeheader()
            writer.writerows(rows)
        print(f'\nFull report written to {args.csv}')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
