#!/usr/bin/env python3
"""library_conflicts.py — folders whose volumes cannot be told apart.

A folder holding several volumes of one franchise is normal:
`/content/ElfQuest` holds six ElfQuest series, `/content/Catwoman` every
Catwoman run. Those are not findings, and asking "do these volumes share a
title" or "does this folder mention this volume's title" says nothing
about them -- against Silas's real library on 2026-09-04 those two
questions produced 250 and 426 findings, and almost all of them were the
library working exactly as intended.

Sharing a directory only costs anything where something has to choose
between its occupants and cannot. `scan_files` walks a volume's folder and
files what it finds against that volume, so where two of them are
indistinguishable, whichever is scanned takes the other's comics and the
loser's issues stay wanted and are downloaded again. Two shapes of that:

    /content/WildC.A.T.S/WildCATS Covert Action Teams
        One Piece (1997) · Hercules (1998) · Grimm Tales of Terror (2018)

share no word at all -- one volume's comics written into another volume's
directory, and eight Grimm Tales of Terror files imported that way.
Whereas

    /content/Spider-Man
        Web of Spider-Man (82) · Web of Spider-Man (83) · (84)

are one title three times over. `match_title` cannot separate them, so
nothing in a filename can, and 24 files were credited to the wrong one.
That question is asked of `match_title` itself rather than of a second
notion of "same series" invented here, so the answer is what the importer
will actually do. Six ElfQuests pass it: `ElfQuest: Jink` is not
`ElfQuest: Wave Dancers`, and neither is `Detective Comics Annual`
`Detective Comics`.

    python scripts/library_conflicts.py --host http://192.168.7.172:5656 \
--api-key KEY

This only reports. Which volume in a group is the misplaced one, and where
it should go instead, is a judgement about a library nobody but its owner
can make -- and moving a folder moves comics. There is no way to delete a
volume here either.
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
from backend.implementations.matching import match_title  # noqa: E402

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
    """The folders more than one volume calls its own.

    Two volumes with the same volume folder is not a matter of degree.
    `scan_files` walks a volume's folder and files what it finds against
    that volume, so whichever of them is scanned writes the other's comics
    to itself, and the loser's issues stay wanted and get downloaded
    again. It is a fact about the library, not a judgement about titles.

    Args:
        volumes (List[Dict[str, Any]]): Every volume in the library.

    Returns:
        List[List[Dict[str, Any]]]: One list per shared folder, the ones
            holding unrelated series first and the fullest first within
            that. A franchise directory is not one of these: thirteen
            ElfQuests under `/content/ElfQuest` each have their own folder
            beneath it, so no two of them share this key.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for volume in volumes:
        if volume.get('folder'):
            grouped[normalise(volume['folder'])].append(volume)

    found = [
        sorted(members, key=lambda v: -(v.get('issues_downloaded') or 0))
        for members in grouped.values()
        if len(members) > 1
        and (unrelated(members) or indistinguishable(members))
    ]

    return sorted(
        found, key=lambda g: (0 if unrelated(g) else 1, -len(g))
    )


def unrelated(members: List[Dict[str, Any]]) -> bool:
    """Whether a folder's volumes have no word in common at all.

    Args:
        members (List[Dict[str, Any]]): The volumes sharing one folder.

    Returns:
        bool: Whether they name different series. `One Piece`, `Hercules`
            and `Grimm Tales of Terror` under the WildCATS path do; the
            six ElfQuest series under `/content/ElfQuest` do not.
    """
    shared = meaningful(members[0]['title'])
    for volume in members[1:]:
        shared &= meaningful(volume['title'])
    return not shared


def indistinguishable(members: List[Dict[str, Any]]) -> bool:
    """Whether two of a folder's volumes are one title to the matcher.

    Sharing a directory is only a problem where something has to choose
    between its occupants and cannot. Six ElfQuest series in
    `/content/ElfQuest` are all separable -- `ElfQuest: Jink` is not
    `ElfQuest: Wave Dancers`, and `match_title` says so -- which is why a
    franchise folder is not a finding however full it gets.

    Two volumes `match_title` calls the same title are a different thing.
    Volumes 82, 83 and 84 of Web of Spider-Man are titled identically and
    all three sit in `/content/Spider-Man`, so nothing in a filename can
    say which one a file is, and 24 of them were credited to the wrong
    volume. Asking `match_title` rather than inventing a second notion of
    "same series" is the point: the answer is what the importer will
    actually do, not a guess about it.

    Args:
        members (List[Dict[str, Any]]): The volumes sharing one folder.

    Returns:
        bool: Whether any two of them cannot be told apart.
    """
    for index, volume in enumerate(members):
        for other in members[index + 1:]:
            if match_title(volume['title'], other['title']):
                return True
    return False


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

    mixed = [g for g in shared if unrelated(g)]
    same = [g for g in shared if not unrelated(g)]

    print(f'{len(volumes)} volumes · {len(mixed)} folder(s) holding '
          f'unrelated series · {len(same)} folder(s) shared by one '
          f'series\' own volumes · {len(wrong)} folder(s) named after a '
          f'different series\n')

    def show(groups: List[List[Dict[str, Any]]]) -> None:
        for members in groups:
            print(f'  {normalise(members[0]["folder"])}')
            for volume in members:
                print(describe(volume))
            print()

    if mixed:
        print('=== one folder, unrelated series: each scans the others\' '
              'comics ===\n')
        show(mixed)

    if same:
        print('=== one folder, one title: nothing can say which volume a '
              'file is ===\n')
        show(same)

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
