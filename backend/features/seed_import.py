# -*- coding: utf-8 -*-

"""Seed-safe library copies for externally managed torrent downloads.

Kapowarr's ``copy`` seeding mode needs an independent library-side directory
entry so that rename/conversion can proceed while the torrent keeps its original
path. Prefer hardlinks when source and library live on the same filesystem, and
fall back to a normal copy per file when they do not (common with some Unraid
share/layout combinations).
"""

from __future__ import annotations

from os import link
from os.path import dirname, isdir, isfile
from shutil import copy2, copytree
from typing import Tuple

from backend.base.files import create_folder
from backend.base.logging import LOGGER


def hardlink_or_copy_file(source: str, target: str) -> bool:
    """Create a library-side hardlink, falling back to a byte copy.

    Returns:
        bool: ``True`` when a hardlink was created, ``False`` when copy fallback
        was required.
    """
    create_folder(dirname(target))
    try:
        link(source, target)
    except OSError:
        copy2(source, target)
        return False
    return True


def hardlink_or_copy_path(source: str, target: str) -> Tuple[int, int]:
    """Copy a torrent root to the library using hardlinks where possible.

    Works for both single-file and directory torrents. Directory trees may be
    mixed: files that can be hardlinked are linked, while only files that cross
    a filesystem/permission boundary are copied.

    Returns:
        Tuple[int, int]: ``(hardlinked_files, copied_files)``.
    """
    hardlinked = 0
    copied = 0

    def _copy_file(src: str, dst: str) -> str:
        nonlocal hardlinked, copied
        if hardlink_or_copy_file(src, dst):
            hardlinked += 1
        else:
            copied += 1
        return dst

    if isfile(source):
        _copy_file(source, target)

    elif isdir(source):
        copytree(source, target, copy_function=_copy_file)

    else:
        raise FileNotFoundError(source)

    LOGGER.info(
        'Prepared seed-safe library copy %s -> %s (%d hardlink(s), %d copied file(s))',
        source, target, hardlinked, copied
    )
    return hardlinked, copied
