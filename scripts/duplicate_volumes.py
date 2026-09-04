#!/usr/bin/env python3
"""duplicate_volumes.py — find the library entries that compete for a file.

A comic whose filename matches two volumes is left alone on purpose:
guessing between them puts it in the wrong folder. Since #198 the matcher
settles that from the issue's own date wherever the two runs disagree about
one, and since #199 orphan recovery falls back to what Kapowarr recorded
fetching the file for. What survives both is the case neither can touch --
one library holding two entries for one run of comics:

    Orphaned downloads:   43 file(s): Penthouse Comix (1997) [id 244]; \
Penthouse Comix (1994) [id 1588]

Reading those ids out of a log and deleting one by hand is how the entry
holding all the files gets deleted. This lists every such pair with what
each side actually has, so the choice is made on the numbers.

It talks to the API rather than the database, so it runs from anywhere that
can reach Kapowarr, against a running instance, without touching the file
the app has open.

    python scripts/duplicate_volumes.py --host http://192.168.7.172:5656 \
--api-key KEY

Nothing is deleted without `--delete-empty`, and that only ever deletes a
volume with no files at all -- an entry holding a single comic is a
judgement call, and it stays yours.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.implementations.matching import match_title  # noqa: E402


def call(host: str, api_key: str, path: str, method: str = 'GET') -> Any:
    """One API call, returning the `result` the app wrapped its answer in."""
    url = f"{host.rstrip('/')}/api{path}"
    url += ('&' if '?' in url else '?') + urlencode({'api_key': api_key})

    request = Request(url, method=method)
    try:
        with urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode('utf-8'))
    except HTTPError as e:
        raise SystemExit(
            f'{method} {path} failed: HTTP {e.code} {e.reason}. '
            f'A 401 means the API key is wrong; find it under '
            f'Settings -> General.'
        )
    except URLError as e:
        raise SystemExit(f'Could not reach {host}: {e.reason}')

    return body.get('result')


def competing_groups(
    volumes: List[Dict[str, Any]]
) -> List[List[Dict[str, Any]]]:
    """Group the volumes that the importer would see as the same series.

    Uses `match_title`, the same predicate the importer uses to decide two
    volumes are candidates for one file, so a group here is exactly a group
    that can produce an ambiguous import.

    Args:
        volumes (List[Dict[str, Any]]): Every volume in the library.

    Returns:
        List[List[Dict[str, Any]]]: The groups of two or more, largest
            first.
    """
    groups: List[List[Dict[str, Any]]] = []
    for volume in volumes:
        for group in groups:
            if match_title(group[0]['title'], volume['title']):
                group.append(volume)
                break
        else:
            groups.append([volume])

    return sorted(
        (g for g in groups if len(g) > 1),
        key=lambda g: (-len(g), g[0]['title'])
    )


def overlapping(group: List[Dict[str, Any]]) -> bool:
    """Whether more than one entry in the group has issues to offer.

    Two volumes only compete for a file if both could hold it. An entry
    with no issues at all cannot claim anything and is not a duplicate,
    just an empty row.
    """
    return sum(1 for v in group if v.get('issue_count')) > 1


def describe(volume: Dict[str, Any]) -> str:
    """One line for one side of a pair, with the numbers the choice needs."""
    size = volume.get('total_size') or 0
    return (
        f"    id {volume['id']:<6} {volume['title']} ({volume['year']})\n"
        f"        {volume.get('issues_downloaded') or 0} of "
        f"{volume.get('issue_count') or 0} issue(s) on disk"
        f" · {size / 1_000_000_000:.2f} GB\n"
        f"        {volume.get('folder')}"
    )


def safe_to_delete(group: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The entries in the group that hold nothing, when another holds
    something.

    Deleting one of these frees every file the pair was blocking and loses
    nothing: the comics are all on the other entry. Where every entry is
    empty, or every entry has files, there is nothing safe to pick and this
    returns nothing.
    """
    empty = [v for v in group if not (v.get('issues_downloaded') or 0)]
    if len(empty) == len(group):
        return []
    return empty


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--host', default=os.environ.get('KAPOWARR_HOST',
                                         'http://localhost:5656'),
        help='Where Kapowarr is, e.g. http://192.168.7.172:5656'
    )
    parser.add_argument(
        '--api-key', default=os.environ.get('KAPOWARR_API_KEY'),
        help='From Settings -> General. Or set KAPOWARR_API_KEY.'
    )
    parser.add_argument(
        '--delete-empty', action='store_true',
        help='Delete the entries that hold no files at all. The volume '
             'folder is never touched, so nothing on disk is lost.'
    )
    args = parser.parse_args()

    if not args.api_key:
        parser.error('--api-key is required (or set KAPOWARR_API_KEY)')

    volumes = call(args.host, args.api_key, '/volumes')
    groups = [g for g in competing_groups(volumes) if overlapping(g)]

    if not groups:
        print(f'{len(volumes)} volumes, no two of which compete for a file.')
        return 0

    print(f'{len(volumes)} volumes · {len(groups)} set(s) where more than '
          f'one entry could claim the same comic\n')

    removable: List[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]] = []
    for group in groups:
        print(f'{group[0]["title"]}')
        for volume in sorted(
            group, key=lambda v: -(v.get('issues_downloaded') or 0)
        ):
            print(describe(volume))

        empty = safe_to_delete(group)
        if empty:
            ids = ', '.join(str(v['id']) for v in empty)
            print(f'    -> id {ids} holds no files; deleting it frees every '
                  f'comic this pair is blocking')
            removable.append((group, empty))
        else:
            print('    -> both sides hold comics; which one is right is '
                  'yours to say')
        print()

    if not removable:
        return 0

    total = sum(len(empty) for _, empty in removable)
    if not args.delete_empty:
        print(f'{total} empty entr(y/ies) could be deleted. Re-run with '
              f'--delete-empty to do it.')
        return 0

    for _, empty in removable:
        for volume in empty:
            call(
                args.host, args.api_key,
                f"/volumes/{volume['id']}?delete_folder=false",
                method='DELETE'
            )
            print(f"Deleted id {volume['id']} "
                  f"{volume['title']} ({volume['year']})")

    print(f'\n{total} deleted. Run Recover Orphaned Downloads to import '
          f'what they were blocking.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
