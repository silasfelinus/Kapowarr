# -*- coding: utf-8 -*-

"""Manual import of files acquired outside of Kapowarr.

This is the "point at a file you already downloaded elsewhere" counterpart to
`backend.features.library_import`'s folder-scanning importers. It deliberately
does not reimplement any matching or import logic: placing a file inside a
volume's folder reuses the same move-then-scan sequence that
`library_import.import_library()` performs on its own proposed matches, and
actually binding a file to an issue reuses
`backend.implementations.file_matching.scan_files()` (filename-based, the
same matcher a normal library scan or continuous import uses) or
`set_file_matching()` (forced match, the same seam the volume page's manual
match UI uses) when the caller already knows which issue a file belongs to.

Because the target volume is already known (picked by the user, or reused
from a Wanted workbench row), no ComicVine lookups happen here -- unlike
`propose_library_import()`, which has to identify an unknown volume first.
"""

from os.path import basename, isfile, join
from typing import List, Union

from backend.base.definitions import ManualImportFileResult, ManualImportResult
from backend.base.files import folder_is_inside_folder, rename_file
from backend.base.logging import LOGGER
from backend.implementations.file_matching import scan_files, set_file_matching
from backend.implementations.volumes import Library


def manual_import_files(
    volume_id: int,
    filepaths: List[str],
    issue_id: Union[int, None] = None
) -> ManualImportResult:
    """Import specific, user-supplied files into a volume, matching them the
    same way the rest of the library-import machinery does.

    Args:
        volume_id (int): The volume the files belong to.

        filepaths (List[str]): Absolute filepaths, already on disk and
            reachable by the Kapowarr server, to import.

        issue_id (Union[int, None], optional): If given, force-match every
            file in `filepaths` to this issue (which must belong to
            `volume_id`) instead of letting the filename-based matcher decide.
            Defaults to None.

    Raises:
        VolumeNotFound: `volume_id` doesn't exist.
        IssueNotFound: `issue_id` is given but doesn't exist, or doesn't
            belong to `volume_id`.

    Returns:
        ManualImportResult: Which files were moved into the volume folder
        and matched (or are pending the next scan), and which were skipped
        (with why).
    """
    volume = Library.get_volume(volume_id)
    volume_data = volume.get_data()

    if issue_id is not None:
        # Raises IssueNotFound if it doesn't exist or isn't part of the
        # volume.
        volume.get_issue(issue_id)

    imported: List[ManualImportFileResult] = []
    skipped: List[ManualImportFileResult] = []
    moved_paths: List[str] = []

    for filepath in filepaths:
        if not isfile(filepath):
            skipped.append({
                'filepath': filepath,
                'status': 'skipped',
                'reason': 'File not found',
                'moved_to': None
            })
            continue

        target = filepath
        if not folder_is_inside_folder(volume_data.folder, filepath):
            target = join(volume_data.folder, basename(filepath))

            if isfile(target) and target != filepath:
                skipped.append({
                    'filepath': filepath,
                    'status': 'skipped',
                    'reason': (
                        'A file with that name already exists in the '
                        f'volume folder: {target}'
                    ),
                    'moved_to': None
                })
                continue

            LOGGER.info(
                f'Manual import: moving {filepath} into volume folder '
                f'as {target}'
            )
            rename_file(filepath, target)

        moved_paths.append(target)
        imported.append({
            'filepath': filepath,
            'status': 'imported',
            'reason': None,
            'moved_to': target if target != filepath else None
        })

    if not moved_paths:
        return {'imported': imported, 'skipped': skipped}

    if issue_id is not None:
        # Force-match every moved file to the chosen issue, reusing the exact
        # forced-match seam the volume page's "Manual Match" UI uses.
        set_file_matching(volume_id, [
            {
                'filepath': path,
                'issue_ids': [issue_id],
                'general_file': False,
                'forced_match': True
            }
            for path in moved_paths
        ])
    else:
        # Let the normal filename-based matcher place the files, same as a
        # regular library scan or the tail end of a folder-based import.
        scan_files(volume_id, filepath_filter=moved_paths, update_websocket=True)

    return {'imported': imported, 'skipped': skipped}
