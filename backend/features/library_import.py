# -*- coding: utf-8 -*-

from asyncio import run, sleep as async_sleep
from glob import glob
from itertools import chain
from os import listdir, walk
from os.path import (abspath, basename, dirname, exists, isdir, isfile,
                     join, splitext)
from threading import Lock
from time import monotonic, sleep
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from backend.base.custom_exceptions import (CVRateLimitReached,
                                            InvalidKeyValue,
                                            VolumeAlreadyAdded)
from backend.base.definitions import (Constants, CVFileMapping, FileConstants,
                                      FilenameData, MonitorScheme,
                                      SpecialVersion)
from backend.base.file_extraction import extract_filename_data
from backend.base.files import (change_basefolder, common_folder,
                                delete_empty_parent_folders,
                                folder_is_inside_folder,
                                list_files, rename_file)
from backend.base.helpers import force_suffix
from backend.base.logging import LOGGER
from backend.features.library_import_metadata import (
    is_library_import_artifact)
from backend.features.library_import_normalization import (
    folder_search_query)
from backend.features.library_import_policy import (
    REVIEW_REASON_NO_CANDIDATE, REVIEW_REASON_TIE, REVIEW_REASON_WEAK_SCORE,
    select_auto_import_volume_result)
from backend.features.metadata import search_volumes_everywhere
from backend.features.tasks import Task, task_library
from backend.implementations.file_matching import scan_files
from backend.implementations.matching import (
    _rank_volume_results_for_file, select_best_volume_result_for_file)
from backend.implementations.naming import mass_rename
from backend.implementations.root_folders import RootFolders
from backend.implementations.volumes import Library
from backend.internals.db import commit
from backend.internals.db_models import FilesDB
from backend.internals.server import (LibraryImportStatusEvent,
                                      TaskStatusEvent, WebSocket)

# ComicVine currently documents 200 requests per resource per hour. Continuous
# import spaces search starts by 20 seconds (at most 180/hour) and counts API /
# import processing time toward that interval instead of sleeping a full 20
# seconds after the work is already finished. The normal review importer keeps
# the existing short brake between searches.
CONTINUOUS_IMPORT_RATE_LIMIT_BACKOFF = 15 * 60

CV_REQUEST_FLOOR = 18.0
"""
The fastest continuous import will ever ask ComicVine: its documented
allowance of 200 requests per resource per hour, and not a request more.
"""

CV_REQUEST_CEILING = 90.0
"The slowest it will throttle itself to, however often ComicVine objects."

CV_BACKOFF_FACTOR = 1.5
CV_RECOVERY_PERIOD = 60 * 60
"An hour without a complaint -- ComicVine's own accounting period."


class AdaptiveDelay:
    """A pacing interval that gives ground when ComicVine pushes back.

    The limit is per resource per hour, and how much of it is left
    depends on what the rest of Kapowarr has been doing: a manual search,
    a refresh, a volume add all draw on the same budget. A fixed interval
    has to assume the worst hour and then pay for that assumption in
    every hour -- which is why this used to sit at 30 seconds, well short
    of the documented rate, for a limit it might never have come near.

    So it starts at the documented rate instead and widens only when
    ComicVine actually objects, easing back once an hour has passed
    without a complaint. Overshooting is expensive here -- a refused
    request costs a fifteen minute cooldown, far more than the seconds a
    tighter interval saves -- so it backs off by half again each time and
    recovers in one step rather than creeping down.
    """

    def __init__(
        self,
        floor: float = CV_REQUEST_FLOOR,
        ceiling: float = CV_REQUEST_CEILING,
        factor: float = CV_BACKOFF_FACTOR,
        recovery_period: float = CV_RECOVERY_PERIOD
    ) -> None:
        self._floor = floor
        self._ceiling = ceiling
        self._factor = factor
        self._recovery_period = recovery_period
        self._current = floor
        self._last_block: Optional[float] = None
        self._lock = Lock()

    def current(self) -> float:
        "The interval to leave before the next request."
        with self._lock:
            if (
                self._last_block is not None
                and monotonic() - self._last_block >= self._recovery_period
            ):
                self._current = self._floor
                self._last_block = None
                LOGGER.info(
                    'An hour without a ComicVine rate limit; import pacing '
                    'back to %.0fs', self._current
                )
            return self._current

    def record_block(self) -> float:
        "ComicVine refused a request. Widen the interval and report it."
        with self._lock:
            self._current = min(self._current * self._factor, self._ceiling)
            self._last_block = monotonic()
            LOGGER.warning(
                'ComicVine rate limit reached; import pacing widened to '
                '%.0fs between requests', self._current
            )
            return self._current

    def reset(self) -> None:
        "Forget the current backoff. Intended for tests."
        with self._lock:
            self._current = self._floor
            self._last_block = None


CV_REQUEST_DELAY = AdaptiveDelay()
"""
Shared by both continuous importers on purpose: ComicVine's budget is one
budget, so two importers pacing themselves independently would each think
they were within it while together they were not.
"""


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
            of a mapping from their filename to their filename data.
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


# How many page images a folder must hold before it is treated as an unpacked
# comic rather than as a folder that happens to contain an image. One is a
# banner, a poster, or a leftover page; a comic has pages.
MIN_UNPACKED_COMIC_IMAGES = 3


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

    # Which folders hold an archive, imported or not. An unpacked comic is a
    # folder of pages; a folder that also contains the .cbz those pages came
    # out of is a folder with leftovers in it, not an unpacked comic.
    archive_folders: Set[str] = {
        abspath(dirname(f))
        for f in all_files
        if f.endswith(FileConstants.CONTAINER_EXTENSIONS)
    }

    # Parse once, and count the images in each folder that could actually be
    # pages -- not cover art, not another reader's thumbnail cache, not
    # anything under a dot-directory.
    parsed_files: Dict[str, FilenameData] = {}
    page_image_counts: Dict[str, int] = {}
    for original_file in all_files:
        if original_file in imported_files:
            continue

        d = abspath(dirname(original_file))
        if d in root_folders:
            continue

        file_data = extract_filename_data(original_file, prefer_folder_year=True)
        parsed_files[original_file] = file_data

        if (
            original_file.endswith(FileConstants.IMAGE_EXTENSIONS)
            and file_data["special_version"] != SpecialVersion.COVER
            and not is_library_import_artifact(original_file)
        ):
            page_image_counts[d] = page_image_counts.get(d, 0) + 1

    def is_unpacked_comic_folder(folder: str) -> bool:
        return (
            folder not in archive_folders
            and page_image_counts.get(folder, 0) >= MIN_UNPACKED_COMIC_IMAGES
        )

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

        file_data = parsed_files[f]

        if (
            f.endswith(FileConstants.IMAGE_EXTENSIONS)
            and file_data["special_version"] != SpecialVersion.COVER
        ):
            if not is_unpacked_comic_folder(d):
                # Decoration, not pages. This used to promote the folder to a
                # work item on the strength of the first loose image in it,
                # whatever that image was -- a series banner in a folder of
                # subfolders, a poster, a reader's `_thumb.jpg`, one page left
                # behind beside its archive. The folder was then searched as
                # though it were a single comic, which is how
                # `/content/Creepy`, `/content/Doctor Who`,
                # `/content/Invincible` and `/content/Future State` -- folders
                # whose actual volumes live in subfolders and were imported
                # long ago -- came back every pass as ties across every
                # same-titled volume the provider has.
                #
                # Nor is the image worth offering on its own: a search for one
                # stray jpg's filename has nothing to match.
                LOGGER.debug(
                    'Ignoring %s: %s holds %d page image(s)%s, which is not an '
                    'unpacked comic',
                    original_file, d, page_image_counts.get(d, 0),
                    ' beside an archive' if d in archive_folders else ''
                )
                continue

            if d in image_folders:
                continue
            image_folders.add(d)
            # An unpacked/image comic is represented by its directory, but the
            # directory remains the scan/checkpoint boundary. Hoisting `d` to
            # its parent collapses every top-level image comic into the entire
            # configured root folder (for example /content), creating one huge
            # synthetic final work item instead of one checkpoint per comic.
            f = d

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
    When continuous mode holds a match, its historical best guess is retained as
    review_candidate so the user can inspect the already-fetched result without
    spending another ComicVine request.
    """
    titles_to_groups: Dict[str, List[int]] = {}
    for group_number, file_group in file_groups.items():
        series_name = next(iter(file_group.values()))['series'].lower()
        titles_to_groups.setdefault(series_name, []).append(group_number)

    cache = search_cache if search_cache is not None else {}
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

        # What the fan-out should keep looking for. Title matching is what
        # it used to stop on, and it is a weaker test than the one applied
        # a few lines below: ComicVine answers almost anything with fifty
        # rows, and a row whose title is exactly right can still be refused
        # on language, type or issue coverage. The search stopped there
        # regardless, so the folder was held having never asked the
        # databases that might have had it. Ask the same question the
        # decision will ask.
        groups = [file_groups[number] for number in titles_to_groups[title]]

        def usable(results: List[Any], groups=groups) -> bool:
            return any(
                _rank_volume_results_for_file(group, results, only_english)
                for group in groups
            )

        cache[title] = await search_volumes_everywhere(title, accepts=usable)
        searches_made += 1

    matches: Dict[int, Dict[str, Any]] = {}
    for title, group_numbers in titles_to_groups.items():
        for group_number in group_numbers:
            review_reason = None
            review_result = None
            if require_confident_match:
                result, review_reason = select_auto_import_volume_result(
                    file_groups[group_number],
                    cache[title],
                    only_english=only_english
                )
                if result is None:
                    review_result = select_best_volume_result_for_file(
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
                review_candidate = None
                if review_result is not None:
                    review_candidate = {
                        'id': review_result['comicvine_id'],
                        'title': (
                            f"{review_result['title']} "
                            f"({review_result['year']})"
                        ),
                        'issue_count': review_result['issue_count'],
                        'link': review_result['site_url']
                    }

                matches[group_number] = {
                    'id': None,
                    'title': None,
                    'issue_count': None,
                    'link': None,
                    'review_reason': (
                        review_reason or REVIEW_REASON_NO_CANDIDATE
                    ),
                    'review_candidate': review_candidate
                }
            else:
                matches[group_number] = {
                    'id': result['comicvine_id'],
                    # GCD never carries a ComicVine ID and Metron only
                    # sometimes does, so `id` alone cannot identify a match
                    # that came from either. Keep the provider's own identity
                    # beside it or the match is unimportable.
                    'provider_id': result.get('provider_id', 'comicvine'),
                    'external_id': result.get(
                        'external_id', result['comicvine_id']
                    ),
                    'title': f"{result['title']} ({result['year']})",
                    'issue_count': result['issue_count'],
                    'link': result['site_url']
                }

    return matches


def match_identifies_a_volume(cv_match: Dict[str, Any]) -> bool:
    """Whether some metadata provider actually recognised this group.

    #139 asked GCD and Metron when ComicVine did not know a title, and
    #140 carried the provider's own identity through to `Library.add` so
    the answer could be imported. The branch that decides whether to
    import at all was never updated: it still asks whether `id` -- the
    ComicVine ID, and nothing else -- is set.

    GCD never carries a ComicVine ID; it has no cross-link, so
    `comicvine_id` is `None` by design. Metron carries one only when the
    series happens to be cross-referenced. So a fallback provider could
    recognise a folder, win the confidence policy outright, and still be
    routed to review -- where, because a winning match dict carries no
    `review_reason`, it was stamped `no-candidate`: "no database in the
    world had this", written about a volume a database had just named.

    Ask the question the import actually needs answered instead. A group
    is importable when any provider identified it, whether or not that
    identity happens to be a ComicVine one.
    """
    return (
        cv_match.get('id') is not None
        or cv_match.get('external_id') is not None
    )


def _volume_owned_folders() -> Set[str]:
    """Every folder a volume in the library already claims.

    Imported lazily so the pure-filesystem helpers below stay testable
    without a database, the same reasoning
    `library_import_policy._library_volume_folders` documents.
    """
    from backend.internals.db import get_db

    return {
        abspath(str(row['folder']))
        for row in get_db().execute(
            "SELECT folder FROM volumes WHERE folder IS NOT NULL "
            "AND folder != '';"
        ).fetchall()
        if row['folder']
    }

def collect_content_less_folders(
    excluded_folders: Optional[Set[str]] = None
) -> List[str]:
    """Find series folders that hold no comics at all.

    Library import has only ever seen a folder as the parent of a file it
    found: `_collect_unimported_files` lists content files, and every
    folder in a pass is `file_to_folder[filepath]` for one of them. A
    folder with nothing in it therefore produces no entry, is never
    grouped, never searched, and never becomes a volume -- even when its
    name says plainly which series it is meant to hold.

    That makes an empty `Blood Train (2025)` invisible rather than a
    request. Treating it as a request is the point: add the volume and
    let the normal monitoring and acquisition machinery fetch the issues.

    Deliberately narrow, because a stale directory looks exactly like an
    intentional one:

    - Leaf directories only. A folder with subdirectories is an organizer
      -- `/content/Batman` above `/content/Batman/Batman (2011)` -- and
      the child is the thing that names a series.
    - Nothing importable inside. Cover art and sidecar metadata do not
      count as content, so a folder holding only `cover.jpg` still
      qualifies; one holding a single `.cbz` does not, because the
      ordinary path already has it.
    - A name that survives `folder_search_query` and contains a letter.
      `/content/2020/` is a shelf, not a series.

    Nothing here decides a match. The caller searches the name and holds
    the result for review; an empty folder never auto-imports, because
    with no files there is no file evidence and the confidence policy has
    nothing to weigh but the title.
    """
    root_folders = {abspath(r) for r in RootFolders().get_folder_list()}
    excluded = set(excluded_folders or set())

    # An empty folder a volume already owns is not a request for that
    # series -- it is that series, waiting for its issues. Kapowarr makes
    # these itself: `create_empty_volume_folders` gives every volume added
    # a folder whether or not anything has been downloaded into it yet, so
    # a library monitoring a thousand volumes it has not filled has a
    # thousand empty folders that all name a series already in it.
    #
    # Without this the first pass would search every one of them and hold
    # every one for review, asking which series a folder means when the
    # library already knows -- a thousand paced provider requests to
    # produce a thousand holds for volumes nobody needed to be told about.
    excluded.update(_volume_owned_folders())

    candidates: List[str] = []
    for root in sorted(root_folders):
        for dirpath, dirnames, filenames in walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            if dirnames:
                # An organizer, not a series folder.
                continue

            folder = abspath(dirpath)
            if folder in excluded or any(
                folder_is_inside_folder(owned, folder)
                for owned in excluded
            ):
                continue

            if is_content_less_series_folder(folder, root_folders, filenames):
                candidates.append(folder)

    return sorted(candidates)


def is_content_less_series_folder(
    folder: str,
    root_folders: Optional[Set[str]] = None,
    filenames: Optional[List[str]] = None
) -> bool:
    """Whether one folder is an empty series folder worth searching for.

    Split out of the walk so the importer can ask the same question again
    when it reaches the folder. A paused pass resumes without the seeding
    scan's state, and re-deciding from the folder itself is both cheaper
    and harder to get out of step than carrying a set across a restart.
    """
    folder = abspath(folder)
    if root_folders is None:
        root_folders = {abspath(r) for r in RootFolders().get_folder_list()}
    if folder in root_folders:
        return False

    if basename(folder).startswith('.'):
        return False

    if filenames is None:
        if not isdir(folder):
            return False
        entries = listdir(folder)
        if any(isdir(join(folder, e)) for e in entries):
            return False
        filenames = [e for e in entries if isfile(join(folder, e))]

    if any(
        f.endswith(FileConstants.CONTENT_EXTENSIONS)
        and not is_library_import_artifact(join(folder, f))
        for f in filenames
    ):
        return False

    query = folder_search_query(folder)
    return bool(query) and any(c.isalpha() for c in query)


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
    rename_files: bool = False,
    continue_on_error: bool = False
) -> Dict[str, List[Dict[str, Any]]]:
    """Add volume to library and import linked files.

    Args:
        matches (List[CVFileMapping]): List of file mappings.

        rename_files (bool, optional): Trigger a rename after importing files.
            Defaults to False.
    """
    LOGGER.info('Starting library import')

    # Keyed by provider identity rather than by ComicVine ID. A GCD match
    # has no ComicVine ID at all, so keying on `id` collapsed every GCD
    # volume in a batch into one `None` bucket.
    volume_to_filepath: Dict[Tuple[str, Any], List[str]] = {}
    volume_identity: Dict[Tuple[str, Any], Tuple[Any, str, Any]] = {}
    for m in matches:
        provider_id = m.get('provider_id') or 'comicvine'
        external_id = m.get('external_id')
        if external_id is None:
            external_id = m['id']
        key = (provider_id, external_id)
        volume_to_filepath.setdefault(key, []).append(m['filepath'])
        volume_identity[key] = (m['id'], provider_id, external_id)
    LOGGER.debug(f'id_to_filepath: {volume_to_filepath}')

    result: Dict[str, List[Dict[str, Any]]] = {
        'imported': [], 'skipped': [], 'failed': []
    }
    root_folders = RootFolders().get_all()
    # The caller is a request thread, and this loop is minutes long for a
    # review list of any size. Without this the page can only show its
    # rotating loading line, which reads as a hang.
    total_items = len(volume_to_filepath)

    def report(item_index: int, files: List[str], cv_id: Any) -> None:
        # The import is the work; the progress line is decoration. A socket
        # that is absent (a task thread with no server, a test) or unhappy
        # must not take a sixty-volume import down with it on entry one.
        try:
            WebSocket().emit(LibraryImportStatusEvent(
                item_index + 1,
                total_items,
                basename(common_folder(files)) or str(cv_id)
            ))
        except Exception:
            LOGGER.debug(
                'Could not emit library import progress', exc_info=True
            )
        return

    for item_index, (key, requested_files) in enumerate(
        volume_to_filepath.items()
    ):
        cv_id, provider_id, external_id = volume_identity[key]
        report(item_index, requested_files, cv_id)
        try:
            # A mapping records where a file was when it was proposed, and
            # importing is not the only thing that moves files: importing any
            # volume relocates its files into the volume folder, and renaming
            # rewrites their basenames. Anything proposed earlier and imported
            # later -- a review hold, a re-submitted list, a second tab -- can
            # therefore name a path that has since moved. Attempting the move
            # anyway raised FileNotFoundError from shutil and failed the whole
            # volume, including the files that were still exactly where the
            # mapping said. Import those; report the rest as already handled.
            files = [f for f in requested_files if exists(f)]
            moved_files = [f for f in requested_files if not exists(f)]
            if not files:
                result['skipped'].append({
                    'id': cv_id, 'filepaths': requested_files,
                    'reason': (
                        'These files are no longer at the paths they were '
                        'found at. They were most likely already imported, '
                        'renamed or moved since this was proposed.'
                    )
                })
                continue

            if moved_files:
                LOGGER.info(
                    'Skipping %d file(s) for ComicVine ID %s that are no '
                    'longer at their recorded paths: %s',
                    len(moved_files), cv_id, moved_files
                )

            for root_folder in root_folders:
                if folder_is_inside_folder(root_folder.folder, files[0]):
                    break
            else:
                result['skipped'].append({
                    'id': cv_id, 'filepaths': files,
                    'reason': 'Files are outside the configured root folders'
                })
                continue

            lcf = common_folder(files)
            if not rename_files and force_suffix(lcf) == root_folder.folder:
                result['skipped'].append({
                    'id': cv_id, 'filepaths': files,
                    'reason': 'The volume folder would equal the root folder'
                })
                continue

            volume_already_added = False
            try:
                volume_id = Library.add(
                    comicvine_id=cv_id,
                    root_folder_id=root_folder.id,
                    monitored=True,
                    monitor_scheme=MonitorScheme.ALL,
                    monitor_new_issues=True,
                    volume_folder=lcf if not rename_files else None,
                    metadata_provider_id=provider_id,
                    metadata_external_id=external_id
                )
                commit()
            except VolumeAlreadyAdded as exc:
                volume_already_added = True
                volume_id = exc.volume_id

            if rename_files or volume_already_added:
                vf = Library.get_volume(volume_id).vd.folder
                if volume_already_added or folder_is_inside_folder(vf, lcf):
                    file_changes = {
                        filepath: (
                            join(vf, basename(filepath))
                            if not folder_is_inside_folder(vf, filepath) else
                            filepath
                        )
                        for filepath in files
                    }
                else:
                    file_changes = change_basefolder(files, lcf, vf)

                moved = []
                for old, new in file_changes.items():
                    if old == new:
                        moved.append(new)
                        continue

                    # Checked immediately before the move, not once up front.
                    # Adding a volume can rescan and relocate files itself, so
                    # a path that existed when this batch started can be gone
                    # by the time its turn comes -- and the resulting
                    # FileNotFoundError escaped `import_library` into the
                    # continuous import task, ending the whole run over one
                    # file that had already been dealt with.
                    if not exists(old):
                        LOGGER.info(
                            'Skipping %s: it moved before it could be '
                            'imported, most likely by an earlier volume in '
                            'this batch', old
                        )
                        continue

                    rename_file(old, new)
                    delete_empty_parent_folders(
                        dirname(old), root_folder.folder
                    )
                    moved.append(new)

                files = moved

            scan_files(volume_id, filepath_filter=files)
            if rename_files:
                mass_rename(volume_id, filepath_filter=files)

            result['imported'].append({
                'id': cv_id,
                'volume_id': volume_id,
                'filepaths': files
            })

        except CVRateLimitReached:
            raise
        except Exception as exc:
            if not continue_on_error:
                raise
            LOGGER.exception(
                'Library import failed for ComicVine ID %s and paths %s',
                cv_id, files
            )
            result['failed'].append({
                'id': cv_id,
                'filepaths': volume_to_filepath[key],
                'reason': str(exc) or exc.__class__.__name__
            })

    return result


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
        self.stop_requested = False
        self.review_folders: Set[str] = set()
        self.review_reasons: Dict[str, int] = {}
        self.review_items: List[Dict[str, Any]] = []
        self.review_group_counter = 0
        self.search_cache: Dict[str, List[Any]] = {}
        self.cv_request_clock: Dict[str, float] = {}
        return

    def request_stop(self) -> None:
        """Ask the importer to stop after its current folder boundary."""
        self.stop_requested = True
        return

    def get_task_details(self) -> Dict[str, Any]:
        """Return review rows only when the single-task API asks for details."""
        return {
            'review_items': list(self.review_items),
            'stop_requested': self.stop_requested
        }

    def _add_review_group(
        self,
        folder: str,
        files: Dict[str, FilenameData],
        cv_match: Dict[str, Any]
    ) -> None:
        """Preserve a held group's best guess for immediate manual review."""
        self.review_group_counter += 1
        review_group = f'continuous-review-{self.review_group_counter}'
        review_candidate = cv_match.get('review_candidate') or {
            'id': None,
            'title': None,
            'issue_count': None,
            'link': None
        }
        review_reason = cv_match.get(
            'review_reason',
            REVIEW_REASON_NO_CANDIDATE
        )

        for filepath in files:
            self.review_items.append({
                'filepath': filepath,
                'file_title': (
                    splitext(basename(filepath))[0]
                    if isfile(filepath) else
                    basename(filepath)
                ),
                'cv': dict(review_candidate),
                'group_number': review_group,
                'folder': folder,
                'review_reason': review_reason
            })
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
        A cooperative stop finishes the current folder boundary before exiting.
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
            if self.stop_requested:
                break

            while not self.stop_requested:
                try:
                    group_to_files = create_groups(folder_files)
                    group_to_cv = run(_match_file_groups(
                        group_to_files,
                        only_english=True,
                        request_delay=CV_REQUEST_DELAY.current(),
                        search_cache=self.search_cache,
                        require_confident_match=True,
                        request_clock=self.cv_request_clock
                    ))

                    matches: List[CVFileMapping] = []
                    folder_needs_review = False
                    folder_review_reasons: Set[str] = set()
                    for group_number, files in group_to_files.items():
                        cv_match = group_to_cv[group_number]
                        if not match_identifies_a_volume(cv_match):
                            folder_needs_review = True
                            review_reason = cv_match.get(
                                'review_reason',
                                REVIEW_REASON_NO_CANDIDATE
                            )
                            folder_review_reasons.add(review_reason)
                            self._add_review_group(
                                folder,
                                files,
                                cv_match
                            )
                            continue

                        matches.extend(
                            {
                                'filepath': filepath,
                                'id': cv_match['id'],
                                'provider_id': cv_match.get(
                                    'provider_id', 'comicvine'
                                ),
                                'external_id': cv_match.get('external_id')
                            }
                            for filepath in files
                        )

                    if matches:
                        import_library(matches, rename_files=False)
                        # By provider identity for the same reason the
                        # import itself groups that way: every GCD match
                        # carries `id: None`, so counting distinct IDs
                        # reported a whole batch of them as one volume.
                        imported += len({
                            (
                                match.get('provider_id') or 'comicvine',
                                match.get('external_id')
                                if match.get('external_id') is not None
                                else match['id']
                            )
                            for match in matches
                        })

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
                    widened = CV_REQUEST_DELAY.record_block()
                    self._emit_status(
                        checked,
                        total,
                        imported,
                        'ComicVine rate limit reached; cooling down for 15 '
                        f'minutes, then {widened:.0f}s between requests'
                    )
                    for _ in range(CONTINUOUS_IMPORT_RATE_LIMIT_BACKOFF):
                        if self.stop_requested:
                            break
                        sleep(1)

        if self.stop_requested:
            self._emit_status(checked, total, imported, 'stopped by user')
        else:
            self._emit_status(checked, total, imported, 'complete')
        return


# frontend.api imports library_import before it imports tasks. Registering here
# makes the new action available to the existing /system/tasks endpoint without
# adding a special-case route or changing the generic task API.
task_library[ContinuousLibraryImport.action] = ContinuousLibraryImport
