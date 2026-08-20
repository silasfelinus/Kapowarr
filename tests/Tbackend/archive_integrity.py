import unittest
from errno import EIO
from io import BytesIO
from os.path import getsize, join
from subprocess import CompletedProcess
from tarfile import DIRTYPE, SYMTYPE, TarInfo, open as tar_open
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from backend.features import archive_integrity as ai
from backend.features.archive_integrity import IntegrityStatus, verify_archive

# A one-pixel-ish payload that actually compresses, so corrupting the
# compressed bytes reliably breaks the CRC rather than being ignored.
PAGE_BYTES = b'\x89PNG\r\n\x1a\n' + (b'kapowarr' * 512)

# A page that does NOT compress, so a gzipped archive of several of them
# is large enough to damage somewhere in the middle rather than in its
# header. Deterministic on purpose -- a random fixture that only
# sometimes reproduces the shape it is named after is worse than none.
INCOMPRESSIBLE_PAGE_BYTES = bytes(
    (i * 2654435761) % 251 for i in range(20000)
)


def _write_zip(path: str, members: dict, compress=ZIP_DEFLATED) -> str:
    with ZipFile(path, 'w', compress) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def _corrupt_payload(path: str) -> str:
    """Flip the bytes of the first member's stored data, leaving every
    header and the central directory untouched.

    Locating the payload exactly matters: damaging the central directory
    instead would make the archive fail to *open*, which is the
    `UNREADABLE` case and not what this fixture is for. What the caller
    gets depends on how the member was written -- ZIP_STORED data reads
    back cleanly and only the CRC disagrees, while a flipped deflate
    stream fails inside zlib. Both are real bit-rot shapes, and both are
    invisible to every name-based check in the codebase today.
    """
    with ZipFile(path) as archive:
        info = archive.infolist()[0]

    with open(path, 'rb') as f:
        raw = bytearray(f.read())

    # Local file header: 30 fixed bytes, then the name and extra fields,
    # whose lengths live at offsets 26 and 28. The central directory's
    # copy of `extra` can differ, so read the local header's own.
    offset = info.header_offset
    name_len = int.from_bytes(raw[offset + 26:offset + 28], 'little')
    extra_len = int.from_bytes(raw[offset + 28:offset + 30], 'little')
    start = offset + 30 + name_len + extra_len

    for i in range(start, start + info.compress_size):
        raw[i] ^= 0xFF

    with open(path, 'wb') as f:
        f.write(bytes(raw))
    return path


def _write_tar(path: str, mode: str, members: dict) -> str:
    """Write a TAR-family archive. `mode` picks the wrapper (`w`, `w:gz`,
    `w:bz2`, `w:xz`) independently of what the filename claims, so the
    mislabelled cases can be built deliberately."""
    with tar_open(path, mode) as archive:
        for name, data in members.items():
            info = TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))
    return path


def _truncate(path: str, keep: float = 0.4) -> str:
    """Cut a file short, the shape a download interrupted part-way
    through leaves behind.

    The default deliberately lands mid-block. A bare tar is a sequence of
    512-byte blocks with no index, so a cut that happens to land exactly
    on a block boundary leaves data that still parses as a complete (if
    shorter) archive -- see
    `test_a_block_aligned_bare_tar_truncation_is_undetectable`.
    """
    with open(path, 'rb') as f:
        raw = f.read()

    keep_bytes = int(len(raw) * keep)
    if keep_bytes % 512 == 0:
        keep_bytes += 100

    with open(path, 'wb') as f:
        f.write(raw[:keep_bytes])
    return path


def _flip_bytes(path: str, offset: int, count: int) -> str:
    with open(path, 'rb') as f:
        raw = bytearray(f.read())

    flipped = 0
    for i in range(offset, min(offset + count, len(raw))):
        raw[i] ^= 0xFF
        flipped += 1

    if not flipped:
        raise AssertionError(
            f'fixture flipped nothing: {path} is only {len(raw)} bytes, '
            f'offset {offset} is past the end'
        )

    with open(path, 'wb') as f:
        f.write(bytes(raw))
    return path


def _corrupt_compressed_body(path: str) -> str:
    """Damage the compressed payload, just past the wrapper's header.

    Offset 32 clears the gzip, bzip2 and xz headers alike while staying
    inside the compressed data, so this is bit-rot in the payload rather
    than a mangled container -- the case only a stream checksum can
    catch.
    """
    return _flip_bytes(path, 32, 64)


def _corrupt_member_payload(path: str) -> str:
    """Damage a bare tar's stored member bytes, leaving headers intact.

    A tar header block is 512 bytes, so starting after the first one
    lands inside the first member's data. Every header, name and size
    field survives, which is exactly why nothing can detect this.
    """
    return _flip_bytes(path, 512 + 64, 256)


def _rar_result(returncode: int, stdout: str = '') -> CompletedProcess:
    return CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=''
    )


class zip_verification(unittest.TestCase):
    def test_a_healthy_cbz_passes(self):
        with TemporaryDirectory() as tmp:
            path = _write_zip(join(tmp, 'comic.cbz'), {
                'pages/01.jpg': PAGE_BYTES,
                'pages/02.jpg': PAGE_BYTES,
                'ComicInfo.xml': b'<ComicInfo/>'
            })
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.OK)
        self.assertTrue(result.ok)

    def test_crc_damage_is_reported_as_corrupt(self):
        # The case upstream #154 is actually about: the archive opens and
        # lists its pages, so every existing code path in Kapowarr thinks
        # it is fine, and only decompressing a member reveals the damage.
        with TemporaryDirectory() as tmp:
            path = _write_zip(join(tmp, 'comic.cbz'), {
                'pages/01.jpg': PAGE_BYTES,
                'pages/02.jpg': PAGE_BYTES
            }, compress=ZIP_STORED)
            _corrupt_payload(path)

            # Precondition: it still opens and still lists its pages, so
            # every name-based check in the codebase thinks it is fine.
            with ZipFile(path) as archive:
                self.assertEqual(len(archive.namelist()), 2)

            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.CORRUPT)
        self.assertFalse(result.ok)

    def test_a_mangled_deflate_stream_is_corrupt_not_an_exception(self):
        # Damage bad enough that zlib can't parse the stream at all
        # raises straight out of `testzip()` instead of being reported.
        with TemporaryDirectory() as tmp:
            path = _write_zip(join(tmp, 'comic.cbz'),
                              {'pages/01.jpg': PAGE_BYTES})
            _corrupt_payload(path)
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.CORRUPT)
        self.assertFalse(result.ok)

    def test_a_truncated_zip_is_unreadable(self):
        with TemporaryDirectory() as tmp:
            path = _write_zip(join(tmp, 'comic.cbz'),
                              {'pages/01.jpg': PAGE_BYTES})
            with open(path, 'rb') as f:
                head = f.read()
            with open(path, 'wb') as f:
                f.write(head[:len(head) // 2])

            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.UNREADABLE)
        self.assertFalse(result.ok)

    def test_an_archive_with_no_pages_is_empty(self):
        # What a stub release looks like: the indexer served something,
        # it unpacks cleanly, and there is no comic in it.
        with TemporaryDirectory() as tmp:
            path = _write_zip(join(tmp, 'comic.cbz'), {
                'readme.txt': b'download more at example.invalid',
                'ComicInfo.xml': b'<ComicInfo/>'
            })
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.EMPTY)
        self.assertFalse(result.ok)

    def test_a_pack_of_nested_comics_passes(self):
        # pack_normalization unwraps these later; an archive whose
        # members are themselves comics is legitimate, not empty.
        with TemporaryDirectory() as tmp:
            path = _write_zip(join(tmp, 'pack.cbz'), {
                'Batman 001.cbz': PAGE_BYTES,
                'Batman 002.cbz': PAGE_BYTES
            })
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.OK)

    def test_directory_entries_do_not_count_as_pages(self):
        with TemporaryDirectory() as tmp:
            path = join(tmp, 'comic.cbz')
            with ZipFile(path, 'w') as archive:
                archive.writestr('pages/', b'')
                archive.writestr('notes', b'no extension')
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.EMPTY)

    def test_an_archive_with_no_files_at_all_is_empty(self):
        with TemporaryDirectory() as tmp:
            path = join(tmp, 'comic.cbz')
            with ZipFile(path, 'w'):
                pass
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.EMPTY)

    def test_pages_in_an_unanticipated_image_format_still_pass(self):
        """The false positive worth caring about. `.bmp`/`.tiff`/`.avif`/
        `.jxl` are not in `FileConstants.IMAGE_EXTENSIONS`, so an
        allowlist rule would call these archives empty -- and empty means
        blocklisted and deleted. Only provably-not-a-page members count
        toward EMPTY."""
        for extension in ('.bmp', '.tiff', '.avif', '.jxl', '.heic'):
            with self.subTest(extension=extension):
                with TemporaryDirectory() as tmp:
                    path = _write_zip(join(tmp, 'comic.cbz'), {
                        f'pages/01{extension}': PAGE_BYTES,
                        f'pages/02{extension}': PAGE_BYTES
                    })
                    result = verify_archive(path)

                self.assertEqual(result.status, IntegrityStatus.OK)
                self.assertTrue(result.ok)

    def test_metadata_alongside_real_pages_does_not_make_it_empty(self):
        with TemporaryDirectory() as tmp:
            path = _write_zip(join(tmp, 'comic.cbz'), {
                'ComicInfo.xml': b'<ComicInfo/>',
                'release.nfo': b'scene notes',
                'pages/01.jpg': PAGE_BYTES
            })
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.OK)

    def test_an_encrypted_zip_is_judged_on_names_without_a_crc_pass(self):
        # `testzip()` raises RuntimeError on encrypted members rather
        # than reporting damage, so the CRC pass is skipped -- but the
        # name list still tells us whether this looks like a comic.
        with TemporaryDirectory() as tmp:
            path = _write_zip(join(tmp, 'comic.cbz'),
                              {'pages/01.jpg': PAGE_BYTES})

            with ZipFile(path) as archive:
                infolist = archive.infolist()
            for info in infolist:
                info.flag_bits |= 0x1

            with patch.object(ai, 'ZipFile') as mock_zipfile:
                handle = mock_zipfile.return_value.__enter__.return_value
                handle.infolist.return_value = infolist
                handle.testzip.side_effect = AssertionError(
                    'testzip() must not be called on an encrypted archive'
                )
                result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.OK)


class rar_verification(unittest.TestCase):
    """RAR goes through the bundled `rar` binary, which isn't runnable in
    this environment, so `run_rar` is mocked the same way
    `tests/Tbackend/comic_reader.py` mocks it."""

    @staticmethod
    def _rar_file(tmp: str) -> str:
        path = join(tmp, 'comic.cbr')
        with open(path, 'wb') as f:
            # A real RAR5 signature, so magic-byte dispatch picks the
            # rar branch rather than falling back to the extension.
            f.write(b'Rar!\x1a\x07\x01\x00' + b'\x00' * 64)
        return path

    def test_a_healthy_rar_passes(self):
        with TemporaryDirectory() as tmp:
            path = self._rar_file(tmp)
            with patch.object(ai, 'run_rar', side_effect=[
                _rar_result(0),
                _rar_result(0, '01.jpg\n02.jpg\n')
            ]):
                result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.OK)

    def test_a_crc_error_exit_code_is_corrupt(self):
        with TemporaryDirectory() as tmp:
            path = self._rar_file(tmp)
            with patch.object(ai, 'run_rar',
                              return_value=_rar_result(3, 'CRC failed')):
                result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.CORRUPT)
        self.assertFalse(result.ok)

    def test_a_password_protected_rar_is_unsupported_not_corrupt(self):
        # Unverifiable is not the same as damaged: blocklisting an
        # encrypted-but-fine release would be a false positive.
        with TemporaryDirectory() as tmp:
            path = self._rar_file(tmp)
            with patch.object(ai, 'run_rar', return_value=_rar_result(11)):
                result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.UNSUPPORTED)
        self.assertTrue(result.ok)

    def test_the_test_command_never_prompts_for_a_password(self):
        # Without `-p-`, `rar t` on a header-encrypted archive blocks on
        # an interactive prompt with no stdin to answer it, hanging
        # post-processing indefinitely.
        with TemporaryDirectory() as tmp:
            path = self._rar_file(tmp)
            with patch.object(ai, 'run_rar', side_effect=[
                _rar_result(0), _rar_result(0, '01.jpg\n')
            ]) as mock_rar:
                verify_archive(path)

        for call_args in mock_rar.call_args_list:
            self.assertIn('-p-', call_args.args[0])

    def test_an_unknown_exit_code_is_unreadable(self):
        with TemporaryDirectory() as tmp:
            path = self._rar_file(tmp)
            with patch.object(ai, 'run_rar', return_value=_rar_result(6)):
                result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.UNREADABLE)

    def test_a_warning_exit_code_still_passes(self):
        with TemporaryDirectory() as tmp:
            path = self._rar_file(tmp)
            with patch.object(ai, 'run_rar', side_effect=[
                _rar_result(1), _rar_result(0, '01.jpg\n')
            ]):
                result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.OK)

    def test_a_rar_with_no_pages_is_empty(self):
        with TemporaryDirectory() as tmp:
            path = self._rar_file(tmp)
            with patch.object(ai, 'run_rar', side_effect=[
                _rar_result(0),
                _rar_result(0, 'readme.txt\n')
            ]):
                result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.EMPTY)


class unverifiable_inputs(unittest.TestCase):
    """Everything this module deliberately declines to judge. Each must
    report `ok`, because a false positive blocklists and deletes a
    release that was fine."""

    def test_a_pdf_is_unsupported(self):
        with TemporaryDirectory() as tmp:
            path = join(tmp, 'comic.pdf')
            with open(path, 'wb') as f:
                f.write(b'%PDF-1.4\n' + b'\x00' * 64)
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.UNSUPPORTED)
        self.assertTrue(result.ok)

    def test_a_loose_image_is_unsupported(self):
        with TemporaryDirectory() as tmp:
            path = join(tmp, 'page.jpg')
            with open(path, 'wb') as f:
                f.write(PAGE_BYTES)
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.UNSUPPORTED)
        self.assertTrue(result.ok)

    def test_a_7z_is_unsupported_because_nothing_here_can_open_one(self):
        with TemporaryDirectory() as tmp:
            path = join(tmp, 'comic.cb7')
            with open(path, 'wb') as f:
                f.write(b'\x37\x7A\xBC\xAF\x27\x1C' + b'\x00' * 64)
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.UNSUPPORTED)
        self.assertTrue(result.ok)

    def test_a_folder_is_unsupported(self):
        # Torrent payloads are still folder-shaped at the point the gate
        # runs; they must pass through, not be condemned.
        with TemporaryDirectory() as tmp:
            result = verify_archive(tmp)

        self.assertEqual(result.status, IntegrityStatus.UNSUPPORTED)
        self.assertTrue(result.ok)

    def test_a_missing_file_is_unsupported(self):
        result = verify_archive('/nonexistent/nope.cbz')

        self.assertEqual(result.status, IntegrityStatus.UNSUPPORTED)
        self.assertTrue(result.ok)

    def test_an_unexpected_exception_never_escapes(self):
        # This runs inside post-processing; an exception here would
        # abandon a download mid-pipeline.
        with TemporaryDirectory() as tmp:
            path = _write_zip(join(tmp, 'comic.cbz'),
                              {'pages/01.jpg': PAGE_BYTES})
            with patch.object(ai, '_verify_zip',
                              side_effect=MemoryError('boom')):
                result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.UNSUPPORTED)
        self.assertTrue(result.ok)


class extension_fallback(unittest.TestCase):
    def test_a_cbz_that_is_really_a_rar_is_verified_as_a_rar(self):
        # Dispatch is on magic bytes, so the mislabelled-extension case
        # `rename_with_proper_extension` exists to fix can't fool it.
        with TemporaryDirectory() as tmp:
            path = join(tmp, 'comic.cbz')
            with open(path, 'wb') as f:
                f.write(b'Rar!\x1a\x07\x00' + b'\x00' * 64)

            with patch.object(ai, 'run_rar',
                              return_value=_rar_result(3)) as mock_rar:
                result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.CORRUPT)
        mock_rar.assert_called()

    def test_a_file_too_short_to_sniff_falls_back_to_its_extension(self):
        # A zero-byte or few-byte .cbz has no signature to match but is
        # exactly the failed-download case worth catching.
        with TemporaryDirectory() as tmp:
            path = join(tmp, 'comic.cbz')
            with open(path, 'wb') as f:
                f.write(b'')
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.UNREADABLE)
        self.assertFalse(result.ok)

    def test_an_html_error_page_saved_as_cbz_is_unreadable(self):
        with TemporaryDirectory() as tmp:
            path = join(tmp, 'comic.cbz')
            with open(path, 'wb') as f:
                f.write(b'<!doctype html><title>404 Not Found</title>')
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.UNREADABLE)
        self.assertFalse(result.ok)


class tar_verification(unittest.TestCase):
    """What a TAR-family archive can and cannot be judged on.

    The split these tests pin down is the whole point of the tar path:
    a compressed tar carries a stream checksum and so can be proven
    damaged, while a bare `.tar` carries nothing and so can never be.
    """

    def test_a_healthy_cbt_passes(self):
        with TemporaryDirectory() as tmp:
            path = _write_tar(join(tmp, 'comic.cbt'), 'w:gz', {
                'pages/01.jpg': PAGE_BYTES,
                'pages/02.jpg': PAGE_BYTES,
                'ComicInfo.xml': b'<ComicInfo/>'
            })
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.OK)
        self.assertTrue(result.ok)

    def test_every_compressed_form_is_verified(self):
        # `.cbt` is only one of the names these arrive under, and
        # `splitext` splits the double extensions in the wrong place, so
        # each one is worth asserting rather than assuming.
        for name, mode in (
            ('comic.cbt', 'w:gz'),
            ('comic.tar.gz', 'w:gz'),
            ('comic.tgz', 'w:gz'),
            ('comic.tar.bz2', 'w:bz2'),
            ('comic.tbz2', 'w:bz2'),
            ('comic.tar.xz', 'w:xz'),
            ('comic.txz', 'w:xz'),
        ):
            with self.subTest(name=name):
                with TemporaryDirectory() as tmp:
                    path = _write_tar(join(tmp, name), mode, {
                        '01.jpg': PAGE_BYTES
                    })
                    healthy = verify_archive(path)

                    _corrupt_compressed_body(path)
                    damaged = verify_archive(path)

                self.assertEqual(healthy.status, IntegrityStatus.OK)
                self.assertEqual(damaged.status, IntegrityStatus.CORRUPT)
                self.assertFalse(damaged.ok)

    def test_a_corrupted_gzip_tar_is_not_silently_downgraded_to_ok(self):
        # The regression this whole path exists for. Verifying a
        # compressed tar by iterating TarFile members instead of reading
        # the stream out does not report damage -- it reports a SHORTER
        # MEMBER LIST, which `_judge_contents` is perfectly happy to call
        # OK. A five-page archive comes back as a healthy one-page
        # archive. Asserting the member list actually shrinks is what
        # makes this a test of the fix rather than of the fixture.
        with TemporaryDirectory() as tmp:
            path = _write_tar(join(tmp, 'comic.cbt'), 'w:gz', {
                f'{i:02}.jpg': INCOMPRESSIBLE_PAGE_BYTES for i in range(1, 6)
            })
            _flip_bytes(path, getsize(path) // 2, 512)

            with tar_open(path, 'r:*') as archive:
                survivors = [m.name for m in archive.getmembers()]

            result = verify_archive(path)

        self.assertLess(
            len(survivors), 5,
            'fixture no longer reproduces the silent-truncation shape'
        )
        # Deliberately not pinned to CORRUPT. Damage this heavy
        # desynchronises deflate so far that the stream hits EOF before
        # its CRC trailer, which is honestly an early end rather than a
        # checksum mismatch -- `test_every_compressed_form_is_verified`
        # is where CORRUPT itself is pinned, on lighter payload bit-rot.
        # What matters here is the invariant: a damaged compressed tar
        # must never come back OK on a member list that damage itself
        # shortened.
        self.assertIn(
            result.status,
            (IntegrityStatus.CORRUPT, IntegrityStatus.UNREADABLE)
        )
        self.assertFalse(result.ok)

    def test_a_truncated_tar_is_unreadable_in_every_form(self):
        for name, mode in (
            ('comic.tar', 'w'),
            ('comic.cbt', 'w:gz'),
            ('comic.tar.bz2', 'w:bz2'),
            ('comic.tar.xz', 'w:xz'),
        ):
            with self.subTest(name=name):
                with TemporaryDirectory() as tmp:
                    path = _write_tar(join(tmp, name), mode, {
                        f'{i:02}.jpg': PAGE_BYTES for i in range(1, 6)
                    })
                    _truncate(path)
                    result = verify_archive(path)

                self.assertEqual(result.status, IntegrityStatus.UNREADABLE)
                self.assertFalse(result.ok)

    def test_a_block_aligned_bare_tar_truncation_is_undetectable(self):
        # The limit of what the bare-tar path can promise, pinned so it
        # is not mistaken for a bug later. A tar is a run of 512-byte
        # blocks with no index and no trailer worth checking, so a cut
        # that lands exactly on a block boundary leaves something that
        # parses as a complete, shorter archive. Only a mid-block cut
        # leaves a partial header for `tarfile` to reject. A compressed
        # tar has no such hole -- its wrapper knows how long the stream
        # should be, which is why the same case above is caught.
        with TemporaryDirectory() as tmp:
            path = _write_tar(join(tmp, 'comic.tar'), 'w', {
                f'{i:02}.jpg': PAGE_BYTES for i in range(1, 6)
            })
            with open(path, 'rb') as f:
                raw = f.read()

            aligned = (len(raw) // 2 // 512) * 512
            with open(path, 'wb') as f:
                f.write(raw[:aligned])

            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.OK)
        self.assertTrue(result.ok)

    def test_a_damaged_bare_tar_passes_because_nothing_can_prove_it(self):
        # Not an oversight, and the reason this is asserted rather than
        # left implicit: a bare tar has no checksum at any level, so a
        # flipped page body reads back with the right member names and
        # the right member sizes. Reporting CORRUPT here would mean
        # blocklisting and deleting on no evidence, which is the one
        # thing this module refuses to do. If a future change makes this
        # test fail by returning CORRUPT, that change is wrong.
        with TemporaryDirectory() as tmp:
            path = _write_tar(join(tmp, 'comic.tar'), 'w', {
                '01.jpg': PAGE_BYTES,
                '02.jpg': PAGE_BYTES
            })
            _corrupt_member_payload(path)

            # Precondition: every header survived, so the archive still
            # reports the right names and the right sizes. This is what
            # makes the damage unprovable, not a weak check.
            with tar_open(path, 'r:*') as archive:
                self.assertEqual(
                    [(m.name, m.size) for m in archive.getmembers()],
                    [('01.jpg', len(PAGE_BYTES)), ('02.jpg', len(PAGE_BYTES))]
                )

            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.OK)
        self.assertTrue(result.ok)

    def test_a_tar_holding_only_junk_is_empty(self):
        with TemporaryDirectory() as tmp:
            path = _write_tar(join(tmp, 'comic.cbt'), 'w:gz', {
                'readme.txt': b'get it at example.invalid',
                'scene.nfo': b'x'
            })
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.EMPTY)
        self.assertFalse(result.ok)

    def test_tar_directory_and_link_members_do_not_count_as_pages(self):
        # Only regular files can be pages. A tar can also carry
        # directories and symlinks, and counting a symlink as content
        # would let an archive of nothing but links look populated.
        with TemporaryDirectory() as tmp:
            path = join(tmp, 'comic.cbt')
            with tar_open(path, 'w:gz') as archive:
                directory = TarInfo('pages.d')
                directory.type = DIRTYPE
                archive.addfile(directory)

                link = TarInfo('01.jpg')
                link.type = SYMTYPE
                link.linkname = '/etc/passwd'
                archive.addfile(link)

            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.EMPTY)
        self.assertFalse(result.ok)

    def test_an_html_error_page_saved_as_cbt_is_unreadable(self):
        with TemporaryDirectory() as tmp:
            path = join(tmp, 'comic.cbt')
            with open(path, 'wb') as f:
                f.write(b'<!doctype html><title>404 Not Found</title>')
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.UNREADABLE)
        self.assertFalse(result.ok)

    def test_a_mislabelled_tar_is_dispatched_on_its_bytes(self):
        # Same bias as the rest of the module: a tar named `.cbz` is
        # still a tar. A bare tar has no signature at offset 0, so this
        # is the one dispatch that has to look at offset 257 instead.
        with TemporaryDirectory() as tmp:
            path = _write_tar(join(tmp, 'comic.cbz'), 'w', {
                '01.jpg': PAGE_BYTES
            })
            result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.OK)
        self.assertTrue(result.ok)

    def test_a_real_io_error_is_never_reported_as_corrupt(self):
        # bz2 signals corruption with a plain OSError, which a genuine
        # disk failure also raises. Only the decompressor leaves errno
        # unset, and getting this backwards would blocklist a good
        # release over a local I/O problem.
        with TemporaryDirectory() as tmp:
            path = _write_tar(join(tmp, 'comic.tar.bz2'), 'w:bz2', {
                '01.jpg': PAGE_BYTES
            })
            disk_failure = OSError(EIO, 'Input/output error')

            with patch.dict(
                ai.TAR_COMPRESSION_OPENERS,
                {'bzip2': lambda *a, **kw: (_ for _ in ()).throw(disk_failure)}
            ):
                result = verify_archive(path)

        self.assertEqual(result.status, IntegrityStatus.UNREADABLE)
        self.assertFalse(result.ok)


if __name__ == '__main__':
    unittest.main()
