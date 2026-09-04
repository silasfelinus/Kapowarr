#!/usr/bin/env python3
"""library_conflicts.py — folders holding comics from unrelated series.

Two volumes of one series is normal, and so is a folder holding several of
them: `/content/ElfQuest` deliberately holds thirteen ElfQuest series,
`/content/Catwoman` every Catwoman run, `/content/Batman` the 1966 and 2016
Batmans and All Star Batman and Robin. A library is organised by franchise,
and neither "these volumes share a title" nor "this folder does not mention
this volume's title" says anything about it -- against Silas's real library
on 2026-09-04 those two questions produced 250 and 426 findings, and almost
every one was the library working exactly as intended.

What is left when those go is one question that survives: does a folder
hold volumes with *no word in common at all*? Thirteen ElfQuests share
"elfquest" and four Batmans share "batman", but

    /content/WildC.A.T.S/WildCATS Covert Action Teams
        One Piece (1997) · Hercules (1998) · Grimm Tales of Terror (2018)

    /content/Black Hammer/Black Hammer Omnibus (2022)
        Black Hammer Omnibus · Superman: The Man of Steel (116 files)

share nothing, and each of those is a folder one volume's comics were
written into under another volume's name. That is how eight Grimm Tales of
Terror files came to import into the WildCATS path.

A volume whose folder sits *inside* another volume's folder counts as part
of that folder's group, which is what catches the third row above.

    python scripts/library_conflicts.py --host http://192.168.7.172:5656 \
--api-key KEY

This only reports. Which volume in such a group is the misplaced one, and
where it should go instead, is a judgement about a library nobody but its
owner can make -- and moving a folder moves comics. There is no way to
delete a volume here either.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Set
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.base.file_extraction import extract_filename_data  # noqa: E402

# Punctuation that separates words in a comic title rather than belonging
# to one. `Batman/Catwoman`, `W.E.B. of Spider-Man` and `Hack/Slash: Body
# Bags` all have to come apart the same way the folder name they were
# written into did.
SEPARATORS = '-.:/,&\'"!?()[]_'


def call(host: str, api_key: str, path: str) -> Any:
    """One API call, returning the `result` the app wrapped its answer in."""
    url = f"{host.rstrip('/')}/api{path}"
    url += ('&' if '?' in url else '?') + urlencode({'api_key': api_key})

    try:
        with urlopen(Request(url), timeout=120) as response:
            return json.loads(response.read().decode('utf-8')).get('result')
    except HTTPError as e:
        raise SystemExit(
            f'GET {path} failed: HTTP {e.code} {e.reason}. A 401 means the '
            f'API key is wrong; find it under Settings -> General.'
        )
    except URLError as e:
        raise SystemExit(f'Could not reach {host}: {e.reason}')


def title_words(title: str) -> Set[str]:
    """The words a title is made of, however it punctuates them.

    Args:
        title (str): A volume title.

    Returns:
        Set[str]: Its words, lowercased. `Batman/Catwoman` and
            `Batman - Catwoman` give the same two.
    """
    cleaned = title
    for character in SEPARATORS:
        cleaned = cleaned.replace(character, ' ')
    return {word for word in cleaned.lower().split() if word}


# Words too common to mean anything. "George R.R. Martin's A Clash of
# Kings" and "Game of Thrones" share "of", and that is not evidence they
# are the same series.
NOISE = frozenset((
    'a', 'an', 'and', 'the', 'of', 'to', 'in', 'on', 'at', 'for', 'vs',
    'versus', 'presents', 'comics', 'comic', 'magazine', 'annual',
    'omnibus', 'collection', 'complete', 'deluxe', 'edition', 'special',
    'volume', 'vol', 'book', 'books', 'series', 'saga', 'tales', 'new'
))


def meaningful(title: str) -> Set[str]:
    """The words of a title that could identify a series."""
    return title_words(title) - NOISE


def shared_folders(
    volumes: List[Dict[str, Any]]
) -> List[List[Dict[str, Any]]]:
    """The folders holding volumes with no meaningful word in common.

    Args:
        volumes (List[Dict[str, Any]]): Every volume in the library.

    Returns:
        List[List[Dict[str, Any]]]: One list per such folder, the fullest
            first. Thirteen ElfQuests share "elfquest" and four Batmans
            share "batman", so a library organised by franchise produces
            nothing here.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for volume in volumes:
        if volume.get('folder'):
            grouped[normalise(volume['folder'])].append(volume)

    found = []
    for members in grouped.values():
        if len(members) < 2:
            continue

        shared = meaningful(members[0]['title'])
        for volume in members[1:]:
            shared &= meaningful(volume['title'])
        if shared:
            continue

        found.append(sorted(
            members, key=lambda v: -(v.get('issues_downloaded') or 0)
        ))

    return sorted(found, key=lambda g: -len(g))


def wrongly_named(volumes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The volumes whose own folder is named after a different series.

    Only the folder's last part is compared, and only for a word in
    common. A volume under a franchise, author or publisher folder keeps
    its own name in its own directory -- `ElfQuest: New Blood` in
    `/content/ElfQuest`, `Marvel Previews` in `/content/Marvel Universe`
    -- so those are not findings. `Golden Kamuy` in
    `/content/Art of, The/Art of Atari (2016)` is.

    Args:
        volumes (List[Dict[str, Any]]): Every volume in the library.

    Returns:
        List[Dict[str, Any]]: The ones to look at, most files first.
    """
    return sorted(
        (
            v for v in volumes
            if v.get('folder')
            and not (
                meaningful(v['title'])
                & meaningful(normalise(v['folder']).rsplit('/', 1)[-1])
            )
        ),
        key=lambda v: -(v.get('issues_downloaded') or 0)
    )


def normalise(folder: str) -> str:
    """One spelling of a path, whatever the platform wrote it as."""
    return folder.replace('\\', '/').rstrip('/')


def describe(volume: Dict[str, Any]) -> str:
    """One line for one volume, with the numbers a decision needs."""
    line = (
        f"    id {volume['id']:<6} {volume['title']} ({volume['year']})  "
        f"{volume.get('issues_downloaded') or 0}/"
        f"{volume.get('issue_count') or 0} on disk"
    )
    own = (volume.get('folder') or '').replace('\\', '/').rstrip('/')
    return line + f'\n        {own}'


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
    args = parser.parse_args()

    if not args.api_key:
        parser.error('--api-key is required (or set KAPOWARR_API_KEY)')

    volumes = call(args.host, args.api_key, '/volumes')
    shared = shared_folders(volumes)
    wrong = wrongly_named(volumes)

    print(f'{len(volumes)} volumes · {len(shared)} folder(s) holding '
          f'unrelated series · {len(wrong)} folder(s) named after a '
          f'different series\n')

    if shared:
        print('=== one folder, unrelated series: each scans the others\' '
              'comics ===\n')
        for members in shared:
            print(f'  {normalise(members[0]["folder"])}')
            for volume in members:
                print(describe(volume))
            print()

    if wrong:
        print('=== the folder is named after a different series ===\n')
        for volume in wrong:
            print(describe(volume))
        print()

    if shared or wrong:
        print('Which volume is the misplaced one, and where it belongs, is '
              'a judgement about your library -- and moving a folder moves '
              'comics. Fix one from the UI, or with:')
        print('  curl -X PUT "$KAP/volumes/<id>?api_key=$KEY" \\')
        print('       -H \'Content-Type: application/json\' \\')
        print('       -d \'{"volume_folder": null}\'')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
