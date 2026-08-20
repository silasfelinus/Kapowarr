# -*- coding: utf-8 -*-

"""
Integrity verification of downloaded comic archives.

Upstream issue #154: damage in a downloaded archive was only ever
discovered in the reader, minutes or months later, as a comic that opens
to zero pages. By then the file is in the library, its issue is no longer
`wanted` (`backend.features.wanted` is a pure projection over
volume/issue/file links), and nothing will ever search for a replacement.
This module answers one question -- *can this archive actually be opened,
and does it plausibly contain a comic?* -- early enough that a bad
download can be blocklisted and re-searched instead of imported.

What it deliberately does NOT do:

- It does not judge *quality* (resolution, page count against the issue's
  real page count, duplicate pages). `backend.features.file_quality` owns
  that, and a low-quality file is still a file; a corrupt one is not.
- It does not repair anything, and it never rewrites the file it is
  handed.
- It does not open formats Kapowarr itself cannot open. `.cb7`/`.7z`,
  `.cbt`/`.tar.gz`, `.epub`/`.mobi` appear in
  `FileConstants.CONTAINER_EXTENSIONS`, but no reader or converter in
  this codebase can read them, so there is no basis on which to call one
  corrupt. Those return `UNSUPPORTED`, which is explicitly *not* a
  failure -- see `IntegrityResult.ok`.

The last point is the module's governing bias, and it is not symmetric:
a false negative costs a corrupt file in the library, which the reader
already surfaces and a user can delete. A false positive blocklists a
release that was fine, deletes it, and steers every future search away
from it. So every branch that cannot *prove* damage reports `OK` or
`UNSUPPORTED`, never `CORRUPT`.

Dispatch is on magic bytes (`get_archive_mimetype`), not on the file
extension. A `.cbz` that is really a RAR is common enough that
post-processing has a whole step for correcting it
(`rename_with_proper_extension`), and reading the real bytes makes this
check independent of where in the pipeline it runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from os.path import splitext
from typing import List, Union
from zipfile import BadZipFile, ZipFile
from zlib import error as zlib_error

from backend.base.definitions import BaseEnum, FileConstants
from backend.base.helpers import run_rar
from backend.base.logging import LOGGER

# `rar t` exit codes worth telling apart. RAR returns 0 on a clean test
# and a small set of documented codes otherwise; only CRC damage is
# positive proof that the payload is bad.
RAR_EXIT_OK = 0
RAR_EXIT_WARNING = 1
RAR_EXIT_CRC_ERROR = 3
RAR_EXIT_WRONG_PASSWORD = 11

# Extensions that are provably not a comic page. Used to decide the
# `EMPTY` verdict, and deliberately a denylist rather than an allowlist
# of image types -- see `_judge_contents` for why that direction matters.
NON_PAGE_EXTENSIONS = frozenset((
    '.txt', '.nfo', '.diz', '.md', '.log',
    '.xml', '.json', '.sfv', '.md5', '.sha1', '.par2',
    '.url', '.html', '.htm', '.db', '.ini'
))


class IntegrityStatus(BaseEnum):
    "The outcome of verifying one archive"

    OK = "ok"
    "Opened cleanly, and holds at least one member that could be content"

    UNSUPPORTED = "unsupported"
    """
    Not a container this codebase can open (7z, tar, epub, a loose image,
    a PDF). Not a failure -- there is nothing to verify against.
    """

    UNREADABLE = "unreadable"
    "Could not be opened at all -- truncated, or not the archive it claims"

    CORRUPT = "corrupt"
    "Opened, but a member failed its CRC check"

    EMPTY = "empty"
    """
    Opened cleanly, but every member is provably not a page -- metadata,
    scene junk, or nothing at all. The stub an indexer serves in place of
    the real release. See `_judge_contents` for why the test is framed as
    "provably not" rather than "recognisably is".
    """


@dataclass(frozen=True)
class IntegrityResult:
    status: IntegrityStatus
    detail: str

    @property
    def ok(self) -> bool:
        """Whether this result should let the download through.

        `UNSUPPORTED` counts as ok: it means "no opinion", not "bad". See
        the module docstring on why this asymmetry is deliberate.
        """
        return self.status in (
            IntegrityStatus.OK,
            IntegrityStatus.UNSUPPORTED
        )


def _result(status: IntegrityStatus, detail: str) -> IntegrityResult:
    return IntegrityResult(status=status, detail=detail)


def _judge_contents(names: List[str]) -> IntegrityResult:
    """Decide whether an archive's member list looks like a comic.

    The test is deliberately inverted. The obvious rule -- "pass only if
    a member has a known image extension" -- would condemn a perfectly
    good comic whose pages happen to be `.bmp`, `.tiff`, `.avif` or
    `.jxl`, none of which are in `FileConstants.IMAGE_EXTENSIONS`, and
    condemning means blocklisted and deleted. So instead an archive is
    only `EMPTY` when every member is *provably* not a page: a known
    metadata/junk extension, or no extension at all. Anything
    unrecognised counts in the archive's favour.

    That still catches what this is for -- an archive holding a readme
    and a link file, or nothing at all -- while a format nobody
    anticipated passes through untouched.

    Args:
        names (List[str]): Member names, directory entries included.

    Returns:
        IntegrityResult: `OK`, or `EMPTY` when nothing inside could be a
        page.
    """
    # A trailing separator is how both `zipfile` and `rar lb` spell a
    # directory entry.
    members = [name for name in names if not name.endswith(('/', '\\'))]

    if not members:
        return _result(IntegrityStatus.EMPTY, 'archive holds no files')

    content = [
        name for name in members
        if splitext(name)[1].lower() not in NON_PAGE_EXTENSIONS
        # A member with no extension at all is junk in practice
        # (`readme`, `file_id`), and never a page.
        and splitext(name)[1]
    ]

    if content:
        return _result(
            IntegrityStatus.OK,
            f'{len(content)} possible content entries'
        )

    return _result(
        IntegrityStatus.EMPTY,
        f'all {len(members)} entries are metadata or junk'
    )


def _verify_zip(filepath: str) -> IntegrityResult:
    """Verify a ZIP-family archive (`.zip`/`.cbz`).

    `ZipFile.testzip()` decompresses every member and compares CRCs --
    the only real corruption check available, and the same one
    `backend.features.backups` already uses on backup archives.

    Args:
        filepath (str): The archive to verify.

    Returns:
        IntegrityResult: The outcome.
    """
    try:
        with ZipFile(filepath, 'r') as archive:
            infolist = archive.infolist()

            # An encrypted member can't be decompressed without the
            # password, and `testzip()` raises RuntimeError rather than
            # reporting damage. Skipping the CRC pass (as
            # `pack_normalization._archive_members` already does) leaves
            # the name-based judgement, which needs no decryption.
            encrypted = any(info.flag_bits & 0x1 for info in infolist)
            if not encrypted:
                try:
                    broken_member = archive.testzip()

                except (zlib_error, EOFError, ValueError) as e:
                    # `testzip()` swallows a plain CRC mismatch and
                    # returns the member name, but a deflate stream
                    # mangled badly enough that zlib can't parse it at
                    # all raises straight out. Both mean the same thing:
                    # the container is fine, the payload is not.
                    return _result(
                        IntegrityStatus.CORRUPT,
                        f'member data could not be decompressed: {e}'
                    )

                if broken_member is not None:
                    return _result(
                        IntegrityStatus.CORRUPT,
                        f'CRC check failed on {broken_member!r}'
                    )

    except (BadZipFile, OSError) as e:
        return _result(
            IntegrityStatus.UNREADABLE,
            f'could not open as zip: {e}'
        )

    return _judge_contents([info.filename for info in infolist])


def _verify_rar(filepath: str) -> IntegrityResult:
    """Verify a RAR-family archive (`.rar`/`.cbr`).

    Args:
        filepath (str): The archive to verify.

    Returns:
        IntegrityResult: The outcome.
    """
    # `-p-` is not optional: without it, `rar t` on a header-encrypted
    # archive blocks on an interactive password prompt, and `run_rar`
    # gives the subprocess no stdin to answer it with. With it, RAR
    # fails fast with RAR_EXIT_WRONG_PASSWORD instead of hanging
    # post-processing forever.
    test = run_rar(['t', '-p-', filepath])

    if test.returncode == RAR_EXIT_CRC_ERROR:
        return _result(
            IntegrityStatus.CORRUPT,
            f'rar reported a CRC error: {test.stderr.strip() or test.stdout.strip()}'
        )

    if test.returncode == RAR_EXIT_WRONG_PASSWORD:
        # Encrypted, and Kapowarr has no password to offer. Unverifiable
        # is not the same as damaged.
        return _result(
            IntegrityStatus.UNSUPPORTED,
            'archive is password-protected; cannot verify'
        )

    if test.returncode not in (RAR_EXIT_OK, RAR_EXIT_WARNING):
        return _result(
            IntegrityStatus.UNREADABLE,
            f'rar exited {test.returncode}: '
            f'{test.stderr.strip() or test.stdout.strip()}'
        )

    listing = run_rar(['lb', '-p-', filepath])
    if listing.returncode != RAR_EXIT_OK:
        return _result(
            IntegrityStatus.UNREADABLE,
            f'rar could not list contents (exit {listing.returncode})'
        )

    return _judge_contents(
        [line for line in listing.stdout.split('\n') if line]
    )


def verify_archive(filepath: str) -> IntegrityResult:
    """Check whether a downloaded file is an openable, plausibly-complete
    comic archive.

    Never raises: an unexpected failure to inspect the file reports
    `UNSUPPORTED` rather than condemning it, on the reasoning in the
    module docstring.

    Args:
        filepath (str): The file to verify.

    Returns:
        IntegrityResult: The outcome. Callers should branch on
        `.ok` rather than on `.status`, so that `UNSUPPORTED` and `OK`
        stay indistinguishable at the call site.
    """
    try:
        archive_type: Union[str, None] = _detect_type(filepath)

        if archive_type == 'zip':
            result = _verify_zip(filepath)
        elif archive_type == 'rar':
            result = _verify_rar(filepath)
        else:
            return _result(
                IntegrityStatus.UNSUPPORTED,
                f'not a verifiable archive type ({archive_type or "unknown"})'
            )

    except Exception as e:
        # Deliberately broad. This runs inside post-processing, where an
        # unexpected exception would abandon a download mid-pipeline --
        # a far worse outcome than declining to verify one file.
        LOGGER.exception(f'Could not verify archive integrity of {filepath}: ')
        return _result(
            IntegrityStatus.UNSUPPORTED,
            f'verification raised {type(e).__name__}'
        )

    if not result.ok:
        LOGGER.warning(
            'Integrity check failed for %s: %s (%s)',
            filepath, result.status.value, result.detail
        )

    return result


def _detect_type(filepath: str) -> Union[str, None]:
    """Identify the archive family by magic bytes, falling back to the
    extension only when the file is too short to sniff.

    Args:
        filepath (str): The file to identify.

    Returns:
        Union[str, None]: `'zip'`, `'rar'`, `'7z'`, or None.
    """
    from backend.base.files import get_archive_mimetype

    archive_type = get_archive_mimetype(filepath)
    if archive_type:
        return archive_type

    # No recognised signature. A file whose extension claims to be a
    # comic archive but whose bytes say otherwise is exactly the
    # truncated/HTML-error-page case worth catching, so report the
    # claimed family and let the opener fail honestly. Anything else
    # (a PDF, a loose image) is genuinely not our business.
    claimed = splitext(filepath)[1].lower().lstrip('.')
    return FileConstants.CB_TO_ARCHIVE_EXTENSIONS.get(
        claimed,
        claimed if claimed in ('zip', 'rar') else None
    )
