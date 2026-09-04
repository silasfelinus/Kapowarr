#!/usr/bin/env python3
"""library_conflicts.py — volumes whose folders disagree with themselves.

Two volumes of one series are normal. Batman has runs from 1940, 2011,
2016 and 2025; Daredevil has nine. Listing every series with more than one
entry says almost nothing, and acting on that list deletes real comics --
`Batman (1940)` shows no files not because it is a duplicate but because
none of its 716 issues have been imported yet.

What *is* mechanically wrong, and what this finds:

  shared-folder     Two or more volumes point at the same directory, so
                    each one's scan sees the other's comics. Four volumes
                    sit in `/content/Batman/Batman (2016)`.

  misfiled-folder   The volume's folder mentions a different series
                    entirely. `One Piece (1997)` points at
                    `/content/WildC.A.T.S/WildCATS Covert Action Teams`,
                    and `Grimm Tales of Terror (2018)` at a release folder
                    underneath it. Both are real, both from Silas's
                    library on 2026-09-04.

Neither is a judgement call, and both have the same fix: ask Kapowarr to
regenerate the folder from its own naming scheme, which moves the volume's
own files there and leaves every other volume's where they are.

    python scripts/library_conflicts.py --host http://192.168.7.172:5656 \
--api-key KEY

Nothing moves without `--fix-folders`, and that only touches the misfiled
ones -- a shared folder can be two entries that both belong there, and
untangling it is a decision, not a repair.

It deliberately offers no way to delete a volume. A volume holding no
files is not evidence of anything: most of the empty ones here are real
series nobody has imported yet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.base.file_extraction import extract_filename_data  # noqa: E402
from backend.implementations.matching import match_title  # noqa: E402


def call(host: str, api_key: str, path: str, method: str = 'GET',
         body: Any = None) -> Any:
    """One API call, returning the `result` the app wrapped its answer in."""
    url = f"{host.rstrip('/')}/api{path}"
    url += ('&' if '?' in url else '?') + urlencode({'api_key': api_key})

    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'

    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode('utf-8')).get('result')
    except HTTPError as e:
        raise SystemExit(
            f'{method} {path} failed: HTTP {e.code} {e.reason}. A 401 means '
            f'the API key is wrong; find it under Settings -> General.'
        )
    except URLError as e:
        raise SystemExit(f'Could not reach {host}: {e.reason}')


def series_of(name: str) -> str:
    """The series a name is about, with a year, volume or issue stripped.

    Uses `extract_filename_data`, so `Amazing Spider-Man, The (1963)` and
    `Howard the Duck v4 (2015)` come back as the series they name rather
    than as themselves. Applied to the volume's title as well as to the
    folder, so a title the parser mangles is mangled identically on both
    sides and still compares equal -- `Gen 13` loses its 13 either way.
    """
    return extract_filename_data(name)['series'] or name


def folder_names_the_volume(title: str, folder: str) -> bool:
    """Whether any part of the path is about the volume's own series.

    A volume folder can sit under a publisher, a franchise or a collection
    -- `/content/Batman/Detective Comics (1937)`, `/content/Moon Knight
    Omnibus (2022)/Moon Knight (1980)` -- so one matching segment anywhere
    in the path is enough. None matching means the path is about something
    else entirely.
    """
    if not folder:
        return False

    wanted = series_of(title)
    return any(
        match_title(wanted, series_of(segment))
        for segment in folder.replace('\\', '/').split('/') if segment
    )


def shared_folders(
    volumes: List[Dict[str, Any]]
) -> List[List[Dict[str, Any]]]:
    """The sets of volumes that were given the same directory.

    Args:
        volumes (List[Dict[str, Any]]): Every volume in the library.

    Returns:
        List[List[Dict[str, Any]]]: One list per shared directory, the
            fullest first.
    """
    by_folder: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for volume in volumes:
        if volume.get('folder'):
            by_folder[volume['folder']].append(volume)

    return sorted(
        (group for group in by_folder.values() if len(group) > 1),
        key=lambda g: -len(g)
    )


def misfiled(volumes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The volumes whose folder is about a different series.

    Args:
        volumes (List[Dict[str, Any]]): Every volume in the library.

    Returns:
        List[Dict[str, Any]]: The ones to regenerate, most files first --
            a volume with comics in the wrong place is more urgent than an
            empty one pointing somewhere odd.
    """
    return sorted(
        (
            v for v in volumes
            if v.get('folder')
            and not folder_names_the_volume(v['title'], v['folder'])
        ),
        key=lambda v: -(v.get('issues_downloaded') or 0)
    )


def describe(volume: Dict[str, Any]) -> str:
    """One line for one volume, with the numbers a decision needs."""
    return (
        f"    id {volume['id']:<6} {volume['title']} ({volume['year']})  "
        f"{volume.get('issues_downloaded') or 0}/"
        f"{volume.get('issue_count') or 0} on disk\n"
        f"        {volume.get('folder')}"
    )


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
        '--fix-folders', action='store_true',
        help='Regenerate the folder of every misfiled volume from the '
             'naming scheme, moving that volume\'s own files into it.'
    )
    args = parser.parse_args()

    if not args.api_key:
        parser.error('--api-key is required (or set KAPOWARR_API_KEY)')

    volumes = call(args.host, args.api_key, '/volumes')
    shared = shared_folders(volumes)
    wrong = misfiled(volumes)

    print(f'{len(volumes)} volumes · {len(shared)} shared folder(s) · '
          f'{len(wrong)} misfiled folder(s)\n')

    if shared:
        print('=== shared-folder: each of these scans the others\' comics '
              '===\n')
        for group in shared:
            print(f'  {group[0]["folder"]}')
            for volume in group:
                print(describe(volume))
            print()

    if wrong:
        print('=== misfiled-folder: the path is about a different series '
              '===\n')
        for volume in wrong:
            print(describe(volume))
        print()

    if not wrong:
        return 0

    if not args.fix_folders:
        print(f'Re-run with --fix-folders to regenerate {len(wrong)} folder'
              f'(s). Each volume\'s own files move with it; nothing else '
              f'is touched.')
        return 0

    for volume in wrong:
        call(
            args.host, args.api_key, f"/volumes/{volume['id']}",
            method='PUT', body={'volume_folder': None}
        )
        print(f"Regenerated id {volume['id']} "
              f"{volume['title']} ({volume['year']})")

    print(f'\n{len(wrong)} folder(s) regenerated.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
