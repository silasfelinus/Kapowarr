# -*- coding: utf-8 -*-

from asyncio import run, sleep as async_sleep
from glob import glob
from itertools import chain
from os.path import abspath, basename, dirname, isfile, join, splitext
from time import monotonic, sleep
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from backend.base.custom_exceptions import (CVRateLimitReached,
                                            InvalidKeyValue,
                                            VolumeAlreadyAdded)
from backend.base.definitions import (CVFileMapping, Constants, FileConstants,
                                      FilenameData, MonitorScheme,
                                      SpecialVersion)
from backend.base.file_extraction import extract_filename_data
from backend.base.files import (change_basefolder, common_folder,
                                delete_empty_parent_folders,
                                folder_is_inside_folder,
                                list_files, rename_file)
from backend.base.helpers import force_suffix
from backend.base.logging import LOGGER
from backend.features.library_import_policy import (
    REVIEW_REASON_NO_CANDIDATE, REVIEW_REASON_TIE, REVIEW_REASON_WEAK_SCORE,
    select_auto_import_volume_result)
from backend.features.tasks import Task, task_library
from backend.implementations.comicvine import ComicVine
from backend.implementations.file_matching import scan_files
from backend.implementations.matching import select_best_volume_result_for_file
from backend.implementations.naming import mass_rename
from backend.implementations.root_folders import RootFolders
from backend.implementations.volumes import Library
from backend.internals.db import commit
from backend.internals.db_models import FilesDB
from backend.internals.server import TaskStatusEvent, WebSocket


# ComicVine currently documents 200 requests per resource per hour. Continuous
# import spaces search starts by 20 seconds (at most 180/hour) and counts API /
# import processing time toward that interval instead of sleeping a full 20
# seconds after the work is already finished. The normal review importer keeps
# the existing short brake between searches.
CONTINUOUS_IMPORT_CV_DELAY = 20.0
CONTINUOUS_IMPORT_RATE_LIMIT_BACKOFF = 15 * 60


def create_groups(
    files: Dict[str, FilenameData]
) -> Dict[int, Dict[str, FilenameData]]:
    """Group files together that seem like they are for the same volume.

    Args:
        files (Dict[str, FilenameData]): The files in the form of a mapping from
            their filename to their filename data.

    Returns:
        Dict[int, Dict[str, FilenameData]]: A mapping from the group number
            (which doesn't cary any meaning except for identifying the group)
            to the files that are in the group, where the files are in the form
            of a mapping from the filename to their filename data.
    """
    group_mapping: Dict[int, FilenameData] = {}
    groups: Dict[int, Dict[str, FilenameData]] = {}

    for file, file_data in files.items():
        match_data = file_data.copy()
        del match_data['issue_number'] # type: ignore

        for group_idx, group_data in group_mapping.items():
            if match_data == group_data:
                groups[group_idx][file] = file_data
                break
        else:
            new_group_number = max(groups or (0,)) + 1
            groups.setdefault(new_group_number, {})[file] = file_data
            group_mapping[new_group_number] = match_data

    LOGGER.debug('File groupings: %s', groups)
    return groups


def _collect_unimported_files(
    folder_filter: Union[str, None] = None,
    limit_parent_folder: bool = False
) -> Tuple[Dict[str, FilenameData], Dict[str, str]]:
    """Collect importable files and map each one to its scan-limit folder.

    Keeping this filesystem pass separate from ComicVine matching lets the
    continuous importer count work without spending API requests and lets it
    skip folders that already need human review.
    """
    root_folders = {
        abspath(r)
        for r in RootFolders().get_folder_list()
    }

    if folder_filter:
        scan_folders = set((
            f
            for f in glob(folder_filter, recursive=True)
            if not isfile(f) # Glob pattern could match to a file
        ))
        for f in scan_folders:
            if not any(folder_is_inside_folder(r, f) for r in root_folders):
                raise InvalidKeyValue('folder_filter', folder_filter)
    else:
        scan_folders = root_folders.copy()

    try:
        # Stable ordering matters for the continuous import conveyor. Without
        # it a changing set iteration order could repeatedly revisit folders.
        all_files = sorted(chain.from_iterable(
            list_files(f, FileConstants.CONTENT_EXTENSIONS)
            for f in scan_folders
        ))

    except NotADirectoryError:
        raise InvalidKeyValue('folder_filter', folder_filter)

    imported_files = {
        f["filepath"]
        for f in FilesDB.fetch()
    }

    image_folders: Set[str] = set()
    unimported_files: Dict[str, FilenameData] = {}
    file_to_folder: Dict[str, str] = {}

    for original_file in all_files:
        if original_file in imported_files:
            continue

        f = original_file
        d = abspath(dirname(f))
        if d in root_folders:
            # File directly in root folder is not allowed
            continue

        file_data = extract_filename_data(f, prefer_folder_year=True)

        if (
            f.endswith(FileConstants.IMAGE_EXTENSIONS)
            and file_data["special_version"] != SpecialVersion.COVER
        ):
            if d in image_folders:
                continue
            image_folders.add(d)
            d, f = dirname(d), d

        scan_folder = dirname(d) if limit_parent_folder else d
        unimported_files[f] = file_data
        file_to_folder[f] = abspath(scan_folder)

    return unimported_files, file_to_folder


async def _match_file_groups(
    file_groups: Dict[int, Dict[str, FilenameData]],
    only_english: bool,
    request_delay: float = Constants.CV_BRAKE_TIME,
    search_cache: Optional[Dict[str, List[Any]]] = None,
    require_confident_match: bool = False,
    request_clock: Optional[Dict[str, float]] = None
) -> Dict[int, Dict[str, Any]]:
    """Match filename groups to ComicVine without swallowing rate limits.

    The old importer used ComicVine.filenames_to_cvs(), which intentionally
    turned a rate-limit response into an empty result. That is convenient for
    some best-effort callers, but disastrous for an importer because a failed
    request becomes indistinguishable from "no match". Import matching is paced
    sequentially here and CVRateLimitReached is allowed to reach the caller.

    Review scans keep the historical best-match suggestion. Continuous import
    asks for calibrated auto-import matches so weak or tied winners are left
    untouched for human review instead of being imported unattended. A shared
    request clock lets continuous mode count work time toward the minimum gap
    between ComicVine search starts instead of adding an extra fixed sleep.
    """
    titles_to_groups: Dict[str, List[int]] = {}
    for group_number, file_group in file_groups.items():
        series_name = next(iter(file_group.values()))['series'].lower()
        titles_to_groups.setdefault(series_name, []).append(group_number)

    cache = search_cache if search_cache is not None else {}
    comicvine = ComicVine()
    searches_made = 0

    for title in titles_to_groups:
        if title in cache:
            continue

        if request_clock is not None:
            last_started = request_clock.get('last_started')
            if last_started is not None:
                elapsed = monotonic() - last_started
                remaining_delay = max(request_delay - elapsed, 0.0)
                if remaining_delay:
                    LOGGER.debug(
                        "Waiting %.2fs before the next ComicVine import search",
                        remaining_delay
                    )
                    await async_sleep(remaining_delay)

            request_clock['last_started'] = monotonic()

        elif searches_made:
            LOGGER.debug(
                "Waiting %ss before the next ComicVine import search",
                request_delay
            )
            await async_sleep(request_delay)

        cache[title] = await comicvine.search_volumes(title)
        searches_made += 1

    matches: Dict[int, Dict[str, Any]] = {}
    for title, group_numbers in titles_to_groups.items():
        for group_number in group_numbers:
            review_reason = None
            if require_confident_match:
                result, review_reason = select_auto_import_volume_result(
                    file_groups[group_number],
                    cache[title],
                    only_english=only_english
                )
            else:
                result = select_best_volume_result_for_file(
                    file_groups[group_number],
                    cache[title],
                    only_english=only_english
                )

            if result is None:
                matches[group_number] = {
                    'id': None,
                    'title': None,
                    'issue_count': None,
                    'link': None,
                    'review_reason': (
                        review_reason or REVIEW_REASON_NO_CANDIDATE
                    )
                }
            else:
                matches[group_number] = {
                    'id': result['comicvine_id'],
                    'title': f"{result['title']} ({result['year']})",
                    'issue_count': result['issue_count'],
                    'link': result['site_url']
                }

    return matches


def count_library_import_folders(
    folder_filter: Union[str, None] = None,
    limit_parent_folder: bool = False,
    excluded_folders: Optional[Set[str]] = None
) -> int:
    """Count unimported folders without making ComicVine requests."""
    _, file_to_folder = _collect_unimported_files(
        folder_filter,
        limit_parent_folder
    )
    excluded = excluded_folders or set()
    return len({
        folder
        for folder in file_to_folder.values()
        if folder not in excluded
    })


def propose_library_import(
    folder_filter: Union[str, None] = None,
    limit: int = 20,
    limit_parent_folder: bool = False,
    only_english: bool = True,
    excluded_folders: Optional[Set[str]] = None,
    request_delay: float = Constants.CV_BRAKE_TIME,
    search_cache: Optional[Dict[str, List[Any]]] = None
) -> List[Dict[str, Any]]:
    """Get list of unimported files and their suggestion for a matching volume
    on CV.

    Args:
        folder_filter (Union[str, None], optional): Only scan the folders that
            match the given value. Can either be a folder or a glob pattern.
            Defaults to None.

        limit (int, optional): The max amount of folders to scan.
            Defaults to 20.

        limit_parent_folder (bool, optional): Base the folder limit on parent
            folder, not folder. Useful if each issue has their own sub-folder.
            Defaults to False.

        only_english (bool, optional): Only match with english releases.
            Defaults to True.

        excluded_folders (Optional[Set[str]], optional): Folder keys to skip.
            Used by continuous import so ambiguous folders do not block later
            work. Defaults to None.

        request_delay (float, optional): Delay in seconds between ComicVine
            searches. Defaults to the normal ComicVine brake time.

        search_cache (Optional[Dict[str, List[Any]]], optional): Reuse prior
            title searches, primarily for continuous import. Defaults to None.

    Raises:
        InvalidKeyValue: The file filter matches to folders outside
            the root folders, or the limit is invalid.
        CVRateLimitReached: ComicVine asked the importer to slow down.

    Returns:
        List[Dict[str, Any]]: The list of files and their matches.
    """
    LOGGER.info('Loading library import')

    if limit < 1:
        raise InvalidKeyValue('limit', limit)

    all_unimported_files, file_to_folder = _collect_unimported_files(
        folder_filter,
        limit_parent_folder
    )
    excluded = excluded_folders or set()

    folders: Set[str] = set()
    unimported_files: Dict[str, FilenameData] = {}
    for f, file_data in all_unimported_files.items():
        folder = file_to_folder[f]
        if folder in excluded:
            continue

        if folder not in folders and len(folders) >= limit:
            continue

        folders.add(folder)
        unimported_files[f] = file_data

    # Preserve the familiar filename-oriented display ordering.
    unimported_files = {
        f: d
        for f, d in sorted(
            unimported_files.items(),
            key=lambda e: basename(e[0])
        )
    }

    group_to_files = create_groups(unimported_files)
    group_to_cv = run(_match_file_groups(
        group_to_files,
        only_english=only_english,
        request_delay=request_delay,
        search_cache=search_cache
    ))

    result = [
        {
            'filepath': file,
            'file_title': (
                splitext(basename(file))[0]
                if isfile(file) else
                basename(file)
            ),
            'cv': group_to_cv[group_number],
            'group_number': group_number,
            'folder': file_to_folder[file]
        }
        for group_number, files in group_to_files.items()
        for file in files
    ]

    return result


def import_library(
    matches: List[CVFileMapping],
    rename_files: bool = False
) -> None:
    """Add volume to library and import linked files.

    Args:
        matches (List[CVFileMapping]): List of file mappings.

        rename_files (bool, optional): Trigger a rename after importing files.
            Defaults to False.
    """
    LOGGER.info('Starting library import')

    cvid_to_filepath: Dict[int, List[str]] = {}
    for m in matches:
        cvid_to_filepath.setdefault(m['id'], []).append(m['filepath'])
    LOGGER.debug(f'id_to_filepath: {cvid_to_filepath}')

    root_folders = RootFolders().get_all()
    for cv_id, files in cvid_to_filepath.items():
        # Find root folder that media is in
        for root_folder in root_folders:
            if folder_is_inside_folder(root_folder.folder, files[0]):
                break
        else:
            continue

        lcf = common_folder(files)
        if not rename_files and force_suffix(lcf) == root_folder.folder:
            # Back out. Volume folder will be equal to root folder.
            continue

        volume_already_added = False

        try:
            volume_id = Library.add(
                comicvine_id=cv_id,
                root_folder_id=root_folder.id,
                monitored=True,
                monitor_scheme=MonitorScheme.ALL,
                monitor_new_issues=True,
                volume_folder=lcf if not rename_files else None
            )
            commit()

        except VolumeAlreadyAdded as e:
            # The volume that the files are for is already in the library, while
            # the files aren't matched to it. This has two reasons:
            # 1. The files are for an existing volume but in a common folder.
            #    Like some users that download files externally and put them all
            #    in a to-be-imported folder. Their idea is that they use LI to
            #    move and rename the files to the proper volume folder because
            #    they don't want to move it themselves, even though the volume
            #    is already in their library.
            # 2. The files matched to the wrong volume, and the wrong volume
            #    happens to already be in the library.
            # The propability of bullet 1 happening is quite low, so moving the
            # files into the volume folder is worth more to users of bullet 1
            # than it is a bad thing for the users that experience bullet 2. So
            # solution is to move the file to the volume folder of the match,
            # and rename if that was chosen.
            volume_already_added = True
            volume_id = e.volume_id

        except CVRateLimitReached:
            # Importers must know that ComicVine throttled us. Silently breaking
            # here made a partial import look like a successful one in the UI.
            raise

        if rename_files or volume_already_added:
            # Move files not already in the volume folder into the volume folder
            vf = Library.get_volume(volume_id).vd.folder

            if volume_already_added or folder_is_inside_folder(vf, lcf):
                file_changes = {
                    f: (
                        join(vf, basename(f))
                        if not folder_is_inside_folder(vf, f) else
                        f
                    )
                    for f in files
                }

            else:
                file_changes = change_basefolder(files, lcf, vf)

            for old, new in file_changes.items():
                if old != new:
                    rename_file(old, new)
                    delete_empty_parent_folders(
                        dirname(old), root_folder.folder
                    )

            files = list(file_changes.values())

        scan_files(volume_id, filepath_filter=files)

        if rename_files:
            # Rename the filenames themselves
            mass_rename(volume_id, filepath_filter=files)

    return


class ContinuousLibraryImport(Task):
    """Slowly auto-import the whole organized library in the background."""

    stop = False
    message = ''
    action = 'continuous_library_import'
    display_title = 'Continuous Library Import'
    category = ''

    @property
    def volume_id(self) -> None:
        return None

    @property
    def issue_id(self) -> None:
        return None

    def __init__(self) -> None:
        self.review_folders: Set[str] = set()
        self.review_reasons: Dict[str, int] = {}
        self.search_cache: Dict[str, List[Any]] = {}
        self.cv_request_clock: Dict[str, float] = {}
        return

    def _emit_status(
        self,
        checked: int,
        total: int,
        imported: int,
        detail: str = ''
    ) -> None:
        remaining = max(total - checked, 0)
        self.message = (
            f'Continuous import: {checked}/{total} folders checked · '
            f'{imported} volumes imported · '
            f'{len(self.review_folders)} need review · '
            f'{remaining} left'
        )

        review_labels = (
            (REVIEW_REASON_TIE, 'tied'),
            (REVIEW_REASON_WEAK_SCORE, 'weak'),
            (REVIEW_REASON_NO_CANDIDATE, 'no candidate')
        )
        review_breakdown = ' · '.join(
            f'{self.review_reasons[reason]} {label}'
            for reason, label in review_labels
            if self.review_reasons.get(reason)
        )
        if review_breakdown:
            self.message += f' · review holds: {review_breakdown}'

        if detail:
            self.message += f' · {detail}'
        WebSocket().emit(TaskStatusEvent(self.message))

    def run(self) -> None:
        """Import a stable folder snapshot while pacing ComicVine searches.

        Existing imported files are Kapowarr's checkpoint. Closing the browser
        does not stop this task, and restarting it later naturally ignores work
        that already made it into the library. Folders with no confident match
        stay untouched for review and cannot jam later folders in this run.
        The shared request clock enforces the minimum interval between ComicVine
        search starts while letting API and import work consume that interval.
        """
        all_files, file_to_folder = _collect_unimported_files()
        folder_to_files: Dict[str, Dict[str, FilenameData]] = {}
        for filepath, file_data in all_files.items():
            folder_to_files.setdefault(
                file_to_folder[filepath], {}
            )[filepath] = file_data

        total = len(folder_to_files)
        checked = 0
        imported = 0
        self._emit_status(checked, total, imported, 'starting')

        for folder, folder_files in folder_to_files.items():
            if self.stop:
                break

            while not self.stop:
                try:
                    group_to_files = create_groups(folder_files)
                    group_to_cv = run(_match_file_groups(
                        group_to_files,
                        only_english=True,
                        request_delay=CONTINUOUS_IMPORT_CV_DELAY,
                        search_cache=self.search_cache,
                        require_confident_match=True,
                        request_clock=self.cv_request_clock
                    ))

                    matches: List[CVFileMapping] = []
                    folder_needs_review = False
                    folder_review_reasons: Set[str] = set()
                    for group_number, files in group_to_files.items():
                        cv_match = group_to_cv[group_number]
                        if cv_match['id'] is None:
                            folder_needs_review = True
                            folder_review_reasons.add(cv_match.get(
                                'review_reason',
                                REVIEW_REASON_NO_CANDIDATE
                            ))
                            continue

                        matches.extend(
                            {
                                'filepath': filepath,
                                'id': cv_match['id']
                            }
                            for filepath in files
                        )

                    if matches:
                        import_library(matches, rename_files=False)
                        imported += len({match['id'] for match in matches})

                    if folder_needs_review:
                        self.review_folders.add(folder)
                        primary_reason = next((
                            reason
                            for reason in (
                                REVIEW_REASON_NO_CANDIDATE,
                                REVIEW_REASON_WEAK_SCORE,
                                REVIEW_REASON_TIE
                            )
                            if reason in folder_review_reasons
                        ), REVIEW_REASON_NO_CANDIDATE)
                        self.review_reasons[primary_reason] = (
                            self.review_reasons.get(primary_reason, 0) + 1
                        )

                    checked += 1
                    self._emit_status(checked, total, imported)
                    break

                except CVRateLimitReached:
                    self._emit_status(
                        checked,
                        total,
                        imported,
                        'ComicVine rate limit reached; cooling down for 15 minutes'
                    )
                    sleep(CONTINUOUS_IMPORT_RATE_LIMIT_BACKOFF)

        if not self.stop:
            self._emit_status(checked, total, imported, 'complete')
        return


# frontend.api imports library_import before it imports tasks. Registering here
# makes the new action available to the existing /system/tasks endpoint without
# adding a special-case route or changing the generic task API.
task_library[ContinuousLibraryImport.action] = ContinuousLibraryImport
