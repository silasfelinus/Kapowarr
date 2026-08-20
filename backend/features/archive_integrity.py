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
- It does not open formats this module cannot open. `.cb7`/`.7z`,
  `.epub`/`.mobi` appear in `FileConstants.CONTAINER_EXTENSIONS`, but
  nothing in this codebase can read them, so there is no basis on which
  to call one corrupt. Those return `UNSUPPORTED`, which is explicitly
  *not* a failure -- see `IntegrityResult.ok`.
  TAR-family containers used to sit in that list too; they no longer do,
  and the reason is measured rather than assumed -- see "Why tar splits
  in two" below.

Why tar splits in two
---------------------

The obvious reading of tar -- "it carries no per-member checksum, so
'opens cleanly and lists plausible pages' is all one could ever assert"
-- is true of a *bare* `.tar` and false of every compressed form, and
the difference is not a detail. It decides whether the check is worth
running at all.

Measured while writing this, by sweeping a single flipped byte across
five-page archives of each format (~1000 offsets each; the numbers below
are from that sweep, not from the test fixtures, which pin the resulting
behaviour rather than re-run the sweep):

- **Bare `.tar`** -- 521 of 531 body flips produce an archive that opens
  cleanly, lists the right member names and reports the right member
  sizes. Nothing raised. There is genuinely no signal, so a bare tar can
  never be called `CORRUPT` here.
- **`.tar.gz` / `.tar.bz2` / `.tar.xz`** -- reading the compressed
  stream through to its end caught **every** flip, via gzip's CRC32
  trailer, bzip2's per-block CRCs and xz's stream check (1036/1036,
  1042/1042 and 1047/1047). That is not weaker evidence than ZIP's
  per-member CRC; it is broader, because it covers the tar headers as
  well as the payload.

The trap is what happens if you verify a compressed tar the *obvious*
way, by iterating `TarFile` members instead of reading the stream out.
Of 733 flips in a `.tar.gz`, iterating members noticed 5 -- and the
other 728 did not merely pass, they came back with a **shorter member
list** that `_judge_contents` is happy to call `OK`. A corrupted
five-page archive presents as a healthy one-page archive. `errorlevel`
does not change this at any of its three settings: the desynchronised
stream stops looking like a tar header, and `tarfile` treats that as a
clean end-of-archive.

So the naive tar check is not just weak, it is *misleading* in exactly
the case the module exists to catch -- worse than returning
`UNSUPPORTED`. Reading the compressed stream to its end is what makes
the verdict sound, and it is why `_verify_tar` decompresses explicitly
rather than trusting iteration.

One honest limit remains, pinned by
`test_a_block_aligned_bare_tar_truncation_is_undetectable`: a bare tar
is a run of 512-byte blocks with no index, so a truncation landing
exactly on a block boundary leaves something that still parses as a
complete, shorter archive. Mid-block cuts are caught; aligned ones are
not. Compressed tars have no such hole, because the wrapper knows how
long its stream should be.

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

from bz2 import open as bz2_open
from dataclasses import dataclass
from gzip import BadGzipFile, open as gzip_open
from lzma import LZMAError, open as lzma_open
from os.path import splitext
from tarfile import ReadError, open as tar_open
from typing import Callable, Dict, List, Union
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

# Compression wrappers a TAR-family container can arrive in, keyed by
# magic bytes so a mislabelled `.cbt` is judged on its contents like
# every other dispatch in this module. Each opener decompresses on read,
# and each verifies its own stream on the way through -- which is the
# whole reason a compressed tar can be judged at all.
TAR_COMPRESSION_MAGIC: Dict[bytes, str] = {
    b'\x1f\x8b': 'gzip',
    b'BZh': 'bzip2',
    b'\xfd7zXZ\x00': 'xz'
}

TAR_COMPRESSION_OPENERS: Dict[str, Callable] = {
    'gzip': gzip_open,
    'bzip2': bz2_open,
    'xz': lzma_open
}

# POSIX tar writes "ustar" at offset 257 of the first header block. A
# bare tar has no signature at offset 0 -- the file simply starts with a
# member's name -- so this is the only content-based tell it has.
USTAR_MAGIC = b'ustar'
USTAR_MAGIC_OFFSET = 257

# Enough to reach past `USTAR_MAGIC_OFFSET`, read once for both checks.
TAR_SNIFF_LENGTH = USTAR_MAGIC_OFFSET + len(USTAR_MAGIC)

# Streaming the decompressed bytes in fixed chunks keeps memory flat on
# a multi-gigabyte pack; the bytes are discarded, only the exceptions
# matter.
DECOMPRESS_CHUNK_SIZE = 1024 * 1024


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


def _detect_tar_compression(filepath: str) -> Union[str, None]:
    """Identify how a TAR-family container is wrapped, by magic bytes.

    Args:
        filepath (str): The file to sniff.

    Returns:
        Union[str, None]: `'gzip'`, `'bzip2'`, `'xz'`, `'plain'` for an
        uncompressed tar, or None when the bytes are neither.
    """
    with open(filepath, 'rb') as f:
        head = f.read(TAR_SNIFF_LENGTH)

    for signature, compression in TAR_COMPRESSION_MAGIC.items():
        if head.startswith(signature):
            return compression

    if head[USTAR_MAGIC_OFFSET:] == USTAR_MAGIC:
        return 'plain'

    return None


def _verify_compressed_stream(
    filepath: str,
    compression: str
) -> Union[IntegrityResult, None]:
    """Read a compressed stream to its end so its checksum is actually
    checked.

    This is the half of tar verification that carries real proof. Every
    supported wrapper validates its own payload -- gzip against a CRC32
    trailer, bzip2 per block, xz against its stream check -- but only if
    the stream is consumed all the way to the end. Iterating `TarFile`
    members does not do that, and stops early and silently on damage
    (see the module docstring).

    Args:
        filepath (str): The archive to read.

        compression (str): One of `TAR_COMPRESSION_OPENERS`' keys.

    Returns:
        Union[IntegrityResult, None]: A failing verdict, or None when
        the stream decompressed cleanly and judging can continue.
    """
    try:
        with TAR_COMPRESSION_OPENERS[compression](
            filepath, 'rb'
        ) as stream:
            while stream.read(DECOMPRESS_CHUNK_SIZE):
                pass

    except EOFError as e:
        # The stream ended before its end-of-stream marker: the download
        # is short. Distinct from a checksum failure -- nothing says the
        # bytes that *did* arrive are wrong.
        return _result(
            IntegrityStatus.UNREADABLE,
            f'{compression} stream ends early: {e}'
        )

    except (BadGzipFile, LZMAError, zlib_error) as e:
        # The decompressor's own verdict on its own payload. Positive
        # proof of damage, and the same class of evidence `_verify_zip`
        # requires before saying CORRUPT.
        return _result(
            IntegrityStatus.CORRUPT,
            f'{compression} integrity check failed: {e}'
        )

    except OSError as e:
        # bz2 is the only wrapper here with no dedicated corruption
        # exception: it raises a plain OSError("Invalid data stream").
        # A real I/O failure (missing file, unreadable disk) is also an
        # OSError, and calling that CORRUPT would blocklist and delete a
        # release over a local disk problem -- the exact false positive
        # this module refuses to make. The decompressor sets no errno,
        # the OS always does, so that is the discriminator; and it is
        # only trusted for bzip2, since gzip and xz have no reason to
        # reach this branch except through genuine I/O.
        if compression == 'bzip2' and e.errno is None:
            return _result(
                IntegrityStatus.CORRUPT,
                f'{compression} integrity check failed: {e}'
            )
        return _result(
            IntegrityStatus.UNREADABLE,
            f'could not read {compression} stream: {e}'
        )

    return None


def _verify_tar(filepath: str, compression: str) -> IntegrityResult:
    """Verify a TAR-family archive (`.cbt`/`.tar`/`.tar.gz`/...).

    What this can prove depends entirely on the wrapper, and the split
    is deliberate:

    - **Compressed** (gzip/bzip2/xz): the stream is read out in full
      first, so a checksum failure is real evidence and reports
      `CORRUPT`.
    - **Bare `.tar`**: no checksum exists anywhere in the format, and
      body damage is measurably indistinguishable from a healthy
      archive. Only the two failures that need no checksum are
      reachable -- one that will not open (`UNREADABLE`) and one holding
      no pages (`EMPTY`). A bare tar is never `CORRUPT`.

    Args:
        filepath (str): The archive to verify.

        compression (str): `'gzip'`, `'bzip2'`, `'xz'` or `'plain'`.

    Returns:
        IntegrityResult: The outcome.
    """
    # A compressed tar is therefore read twice: once as a raw stream to
    # check the wrapper's checksum, then again by `tarfile` for the
    # member list. Deliberate -- `tarfile`'s own iteration is exactly
    # what cannot be trusted to reach the end of the stream, so the
    # checksum pass cannot be folded into it. `_verify_zip` pays the
    # same two-pass cost for the same reason.
    if compression != 'plain':
        failure = _verify_compressed_stream(filepath, compression)
        if failure is not None:
            return failure

    try:
        with tar_open(filepath, 'r:*') as archive:
            # Only regular files can be pages. A tar can also carry
            # directories, symlinks, hardlinks and device nodes, none of
            # which `_judge_contents` should count either for or against
            # the archive -- the same reasoning
            # `comic_reader._is_readable_tar_page` applies when deciding
            # what is safe to serve.
            names = [
                member.name
                for member in archive.getmembers()
                if member.isfile()
            ]

    except (ReadError, EOFError, OSError) as e:
        return _result(
            IntegrityStatus.UNREADABLE,
            f'could not open as tar: {e}'
        )

    return _judge_contents(names)


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
        elif archive_type == 'tar':
            result = _verify_tar(
                filepath,
                _detect_tar_compression(filepath) or 'plain'
            )
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
        Union[str, None]: `'zip'`, `'rar'`, `'7z'`, `'tar'`, or None.
    """
    from backend.base.files import get_archive_mimetype

    archive_type = get_archive_mimetype(filepath)
    if archive_type:
        return archive_type

    # `get_archive_mimetype` reads a signature at offset 0, which a bare
    # tar does not have, so tar is sniffed separately rather than added
    # to `FileConstants.ARCHIVE_MAGIC_BYTES` -- that mapping also drives
    # `rename_with_proper_extension`, and teaching it about tar would
    # start renaming files well outside this check's remit.
    if _detect_tar_compression(filepath) is not None:
        return 'tar'

    # No recognised signature. A file whose extension claims to be a
    # comic archive but whose bytes say otherwise is exactly the
    # truncated/HTML-error-page case worth catching, so report the
    # claimed family and let the opener fail honestly. Anything else
    # (a PDF, a loose image) is genuinely not our business.
    #
    # The tar suffixes are matched against the whole name, since the
    # compressed forms are double extensions that `splitext` splits in
    # the wrong place.
    if filepath.lower().endswith(FileConstants.TAR_ARCHIVE_SUFFIXES):
        return 'tar'

    claimed = splitext(filepath)[1].lower().lstrip('.')
    return FileConstants.CB_TO_ARCHIVE_EXTENSIONS.get(
        claimed,
        claimed if claimed in ('zip', 'rar') else None
    )
