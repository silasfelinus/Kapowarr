# -*- coding: utf-8 -*-

"""
Handling of all the matching done between files, the database, search results,
issues and volumes.
"""

from __future__ import annotations

from itertools import chain
from difflib import SequenceMatcher
from math import floor
from re import compile
from typing import TYPE_CHECKING, Dict, List, Mapping, Tuple, Union

from backend.base.definitions import IssueData, SpecialVersion, VolumeMetadata
from backend.base.file_extraction import special_version_regex
from backend.base.helpers import force_range, normalise_query_string
from backend.implementations.blocklist import blocklist_contains

if TYPE_CHECKING:
    from backend.base.definitions import (FilenameData, SearchResultData,
                                          SearchResultMatchData, VolumeData)

# `\bthe\b`, not `\bthe\s`: a great many libraries file a series under its
# trailing article -- "Immortal Hulk, The", "Crow - Pestilence, The", and in
# this shape all the way up to a folder named "Art of, The". A "the" at the end
# of a name has no whitespace after it, so the old pattern left it in place
# while stripping the leading "the" from the ComicVine title, and the two could
# never be equal. `?` is stripped for the same reason: a folder name cannot
# carry one on most filesystems, so "Are You Afraid of Darkseid" could never
# reach "Are You Afraid of Darkseid?". Neither character distinguishes one
# series from another, and spaces are removed straight afterwards anyway.
clean_title_regex = compile(
    r'((?<=annual)s|/|\-|–|\+|,|\.|\!|\?|:|\bthe\b|\band\b|&|’|\'|\"|\bone[\-\s]?shot\b|\bhard[\-\s]?cover\b|\bomnibus\b|\btpb\b)'
)

# How much `rate_search_result` disprefers a candidate whose issue count
# cannot reach the highest issue number in the files. Named so a caller that
# weighs the same evidence with its own policy can tell how much of a base
# score is already this penalty, instead of charging for it a second time.
ISSUE_CAPACITY_RATING_PENALTY = 1

# The classifications `determine_special_version` can reach on its own,
# every one of them meaning "this volume is a single issue".
SINGLE_ISSUE_VERSIONS = (
    SpecialVersion.TPB,
    SpecialVersion.ONE_SHOT,
    SpecialVersion.HARD_COVER,
    SpecialVersion.OMNIBUS
)


def match_title(
    title1: str,
    title2: str,
    allow_contains: bool = False
) -> bool:
    """Determine if two titles match; if they refer to the same thing.

    Args:
        title1 (str): The first title.
        title2 (str): The second title, to which the first title should be
            compared.
        allow_contains (bool, optional): Also match when title2 is found
            somewhere in title1.

    Returns:
        bool: Whether the titles match.
    """
    clean_reference_title = _clean_for_comparison(title1)
    clean_title = _clean_for_comparison(title2)

    if allow_contains:
        return clean_title in clean_reference_title
    else:
        return clean_reference_title == clean_title


# A near-title candidate is only ever considered when strict matching found
# nothing at all, so a folder that matches today is unaffected. These bound
# how far "nothing at all" is allowed to reach.
NEAR_TITLE_MIN_LENGTH = 3
NEAR_TITLE_MIN_SHARED_RATIO = 0.5
NEAR_TITLE_MAX_SHORT_REMAINDER = 2
NEAR_TITLE_MIN_SIMILARITY = 0.9

_parenthetical_regex = compile(r'\s*\([^()]*\)\s*')


_word_split_regex = compile(r'[^a-z0-9]+')


def _clean_for_comparison(title: str) -> str:
    """Reduce a title to the form `match_title` compares."""
    return clean_title_regex.sub(
        '',
        normalise_query_string(title).lower()
    ).replace(' ', '')


def match_title_nearly(title1: str, title2: str) -> bool:
    """Whether two titles are close enough to be worth scoring.

    `match_title` is exact equality after cleaning, and it is the first
    filter every candidate passes through. That is right when the parsed
    series is the series -- and a real library is full of folders where
    it is not. ComicVine files subtitles the folder does not have
    ("Burn the Orphanage" against "Burn the Orphanage: Reign of Terror"),
    the parser keeps a trailing issue number in the series
    ("Detective Comics 074"), a scene release carries a phase or edition
    the database does not, and both sides disagree about a plural. In
    every one of those cases the correct volume was in the response and
    was discarded before anything could weigh it, so the folder was held
    as `no-candidate` -- "no database has this" -- on every pass forever.

    This does not decide a match. It decides whether the ranker is
    allowed to look at one, and it is consulted only when strict matching
    admitted nobody. The score, margin and tie rules then apply
    unchanged, so an ambiguous near-title folder is still held for a
    human -- which is the right answer for "Future State" against a shelf
    of `Future State: <somewhere>` volumes.

    Deliberately not symmetric with `allow_contains`: a title that merely
    appears somewhere inside the other matches "Sex Cells" to "Cells" and
    "Junji Ito's Frankenstein" to "Frankenstein". Only a shared *start*
    counts, and only when it covers enough of the longer title.
    """
    # ComicVine hangs editions and sub-editions off a title in
    # parentheses -- "(DC Essential Edition)", "Ho!(liday)" -- which the
    # folder never carries. Compare with them and without.
    for candidate in (title2, _parenthetical_regex.sub(' ', title2)):
        if _near_enough(title1, candidate):
            return True
    return False


def _near_enough(title1: str, title2: str) -> bool:
    first = _clean_for_comparison(title1)
    second = _clean_for_comparison(title2)
    if not first or not second:
        return False

    if first == second:
        return True

    if min(len(first), len(second)) < NEAR_TITLE_MIN_LENGTH:
        return False

    # Word counts have to come from the spaced form: the comparison form
    # has the spaces removed, because "Gen 13" and "Gen13" are the same
    # series.
    word_counts = {
        first: len(_words(title1)),
        second: len(_words(title2))
    }

    shorter, longer = sorted((first, second), key=len)
    if longer.startswith(shorter):
        extra = longer[len(shorter):]
        # A trailing issue number the parser kept in the series, or a
        # one- or two-character ornament ("ODY" against "ODY-C"): the
        # shared start is the whole of the shorter title.
        if extra.isdigit() or len(extra) <= NEAR_TITLE_MAX_SHORT_REMAINDER:
            return True

        # Otherwise the shorter title has to be a real multi-word phrase
        # before a shared start means anything. One word in common is how
        # "The Lust of Us" reaches "Lust" and "Sleep Deprivation Ninja"
        # reaches "Sleep" -- a different comic that happens to begin the
        # same way.
        if word_counts[shorter] < 2:
            return False

        return len(shorter) / len(longer) >= NEAR_TITLE_MIN_SHARED_RATIO

    # Neither starts the other, but they may differ only in a plural or a
    # stray character: "The Monsters Makers" against "The Monster Makers".
    return SequenceMatcher(
        None, first, second
    ).ratio() >= NEAR_TITLE_MIN_SIMILARITY


def _words(title: str) -> List[str]:
    """Split a title into its meaningful words."""
    cleaned = clean_title_regex.sub(
        '',
        normalise_query_string(title).lower()
    )
    return [word for word in _word_split_regex.split(cleaned) if word]


def match_year(
    reference_year: Union[int, None],
    check_year: Union[int, None],
    end_year: Union[int, None] = None,
    conservative: bool = False
) -> bool:
    """Check if two years match, with one year of 'wiggle room'.

    Args:
        reference_year (Union[int, None]): The year to check against.

        check_year (Union[int, None]): The year to check.

        end_year (Union[int, None], optional): A different year as the end
            border. Supply `None` to disable and use reference_year for both
            borders instead.
            Defaults to None.

        conservative (bool, optional): If either of the years is `None`, play it
            safe and return `True`.
            Defaults to False.

    Returns:
        bool: Whether the years match.
    """
    if reference_year is None or check_year is None:
        return conservative

    end_border = end_year or reference_year

    return reference_year - 1 <= check_year <= end_border + 1


def match_volume_number(
    volume_data: VolumeData,
    volume_issues: List[IssueData],
    check_number: Union[Tuple[int, int], int, None],
    conservative: bool = False
) -> bool:
    """Check whether the volume number matches the one of the volume or its year.
    If Special Version is VAI, then the volume number (or range) should match to
    an issue number in the volume.

    Args:
        volume_data (VolumeData): The data of the volume.

        volume_issues (List[IssueData]): The data of the issues of the volume.

        check_number (Union[Tuple[int, int], int, None]): The volume number
            (or range) to check.

        conservative (bool, optional): If either of the volume numbers is `None`,
            play it safe and return `True`.
            Defaults to False.

    Returns:
        bool: Whether the volume numbers match.
    """
    if (volume_data.volume_number, volume_data.year) == (None, None):
        return conservative

    if check_number is None:
        return conservative

    if isinstance(check_number, int):
        if check_number == volume_data.volume_number:
            return True

        if match_year(volume_data.year, check_number):
            return True

    # Volume numbers don't match, but
    # it's possible that the volume is volume-as-issue.
    # Then the volume number is actually the issue number.
    # So check whether an issue exists with the volume number.

    if volume_data.special_version != SpecialVersion.VOLUME_AS_ISSUE:
        return False

    number_found = 0
    numbers = (
        check_number
        if isinstance(check_number, tuple) else
        (check_number,)
    )
    for issue in volume_issues:
        if issue.calculated_issue_number in numbers:
            number_found += 1

    return number_found == len(numbers)


def match_special_version(
    reference_version: Union[SpecialVersion, str, None],
    check_version: Union[SpecialVersion, str, None],
    volume_title: str,
    issue_number: Union[Tuple[float, float], float, None] = None
) -> bool:
    """Check if Special Versions match. Takes into consideration that files
    have lacking state specificity.

    Args:
        reference_version (Union[SpecialVersion, str, None]): The state to check
            against.

        check_version (Union[SpecialVersion, str, None]): The state to check.

        volume_title (str): The title of the volume.

        issue_number (Union[Tuple[float, float], float, None], optional): The
            issue number to check for if applicable. E.g. so that
            issue_number == 1 and special_version == 'one-shot' | 'hard-cover'
            will match.
            Defaults to None.

    Returns:
        bool: Whether the states match.
    """
    if check_version in (
        reference_version,
        SpecialVersion.COVER,
        SpecialVersion.METADATA
    ):
        return True

    if (
        issue_number == 1.0
        and reference_version in (
            SpecialVersion.HARD_COVER,
            SpecialVersion.ONE_SHOT,
            SpecialVersion.OMNIBUS,
            # TPB belongs here for the same reason the other three do, and
            # its absence was the single largest source of files the
            # library scan silently refused.
            #
            # `determine_special_version` calls any volume with exactly one
            # issue released over a month ago a TPB -- explicitly a guess
            # ("we'll assume it's a TPB"), and the one every one-shot,
            # special and graphic novel in a library falls into. Such a
            # volume's own file is ordinarily named `<Series> 01 (year)`,
            # which parses as issue 1 with no special version. Every other
            # single-issue classification accepted that file through this
            # branch. TPB alone dropped it, so the volume was created,
            # its file was refused, nothing entered `files`, and the
            # folder stayed untracked and came back on every rescan.
            SpecialVersion.TPB
        )
    ):
        return True

    if (
        reference_version == SpecialVersion.VOLUME_AS_ISSUE
        and check_version == SpecialVersion.NORMAL
    ):
        return True

    if (
        "omnibus" in volume_title.lower()
        and check_version == SpecialVersion.OMNIBUS
    ):
        return True

    if (
        check_version in COLLECTED_EDITION_MATCH
        and reference_version == SpecialVersion.NORMAL
        and issue_number is None
    ):
        # A collected edition sitting in a normal volume's folder. The file is
        # the whole run rather than one issue of it, so it belongs to this
        # volume even though the volume itself is not a special version.
        return True

    # Volume's Special Version could be one that often isn't explicitly
    # mentioned in the filename or that isn't possible to determine from the
    # filename. EF will determine the file to be a TPB in such scenario.
    return (
        check_version == SpecialVersion.TPB
        and reference_version in (
            SpecialVersion.HARD_COVER,
            SpecialVersion.ONE_SHOT,
            SpecialVersion.OMNIBUS,
            SpecialVersion.VOLUME_AS_ISSUE
        )
    )


def folder_extraction_filter(
    file_data: FilenameData,
    volume_data: VolumeData,
    volume_issues: List[IssueData],
    end_year: Union[int, None]
) -> bool:
    """The filter applied to the files when extracting from a folder,
    which decides whether a file is relevant or not.

    Args:
        file_data (FilenameData): Extracted data from file.
        volume_data (VolumeData): The data of the volume.
        volume_issues (List[IssueData]): The data of the issues of the volume.
        end_year (Union[int, None]): The year of last issue or volume year.

    Returns:
        bool: Whether the file should be kept or not.
    """
    annual = 'annual' in volume_data.title.lower()
    matching_annual = file_data['annual'] == annual

    matching_title = match_title(
        file_data['series'],
        volume_data.title
    )

    matching_year = match_year(
        volume_data.year,
        file_data['year'],
        end_year
    )

    matching_volume_number = match_volume_number(
        volume_data,
        volume_issues,
        file_data['volume_number'],
    )

    matching_special_version = match_special_version(
        volume_data.special_version,
        file_data['special_version'],
        volume_data.title,
        file_data['issue_number']
    )

    # Neither are found (we play it safe so we keep those)
    neither_found = (
        file_data['year'], file_data['volume_number']
    ) == (None, None)

    return (
        matching_title
        and matching_annual
        and matching_special_version
        and (
            matching_year
            or matching_volume_number
            or neither_found
        )
    )


def collected_edition_of_volume(
    file_data: FilenameData,
    volume_data: VolumeData
) -> bool:
    """Whether the file is a collected edition belonging to a normal volume.

    An omnibus in the series' own folder is not one issue of that series, so
    it must not be bound to issue one -- the rest would read as missing and
    Kapowarr would go download comics that are already on disk inside this
    very file.

    It is not bound to every issue either, tempting as that is. Plenty of
    collections cover only part of a run, and nothing in a folder name
    reliably says which: "Black Science Omnibus - The Beginner's Guide to
    Entropy" collects roughly a third of Black Science and announces none of
    that. Marking the whole run as had on that evidence would quietly strand
    every issue the book does not contain.

    So it is filed as a volume file instead. It sits in the folder and is
    visible in the Files window, while the individual issues stay wanted and
    are fetched normally. That means a large collected file alongside the
    issues it duplicates, which is the deliberate trade: redundant bytes over
    a silently incomplete series.
    """
    return (
        volume_data.special_version == SpecialVersion.NORMAL
        and file_data['special_version'] in COLLECTED_EDITION_MATCH
        and file_data['issue_number'] is None
    )


def file_importing_filter(
    file_data: FilenameData,
    volume_data: VolumeData,
    volume_issues: List[IssueData],
    number_to_year: Mapping[float, Union[int, None]]
) -> bool:
    """Filter for matching files to volumes.

    Args:
        file_data (FilenameData): Extracted data from file.
        volume_data (VolumeData): The data of the volume.
        volume_issues (List[IssueData]): The data of the issues of the volume.

    Returns:
        bool: Whether the file matches to the volume or not.
    """
    if file_data['issue_number'] is not None:
        issue_number = file_data['issue_number']

    elif (
        volume_data.special_version == SpecialVersion.VOLUME_AS_ISSUE
        and file_data['volume_number'] is not None
    ):
        issue_number = file_data['volume_number']

    else:
        issue_number = float('-inf')

    matching_special_version = match_special_version(
        volume_data.special_version,
        file_data['special_version'],
        volume_data.title,
        file_data['issue_number']
    )

    matching_volume_number = match_volume_number(
        volume_data,
        volume_issues,
        file_data['volume_number']
    )

    matching_year = match_year(
        volume_data.year,
        file_data['year'],
        number_to_year.get(force_range(issue_number)[-1])
    )

    # `determine_special_version` calls any volume with one issue released
    # over a month ago a TPB -- its own comment says "we'll assume" -- and
    # that assumption then refuses the volume's own files. #153 opened it
    # for issue 1; the live library shows the rest of the shape: five
    # volumes whose single issue is numbered `00` rather than `01`,
    # `Doctor Strange 450` against a volume that has since grown well past
    # one issue, `Witch Hammer` 2 and 3 of a series that outran its
    # catalogue entry.
    #
    # A guess must not outrank the files it was guessing about, so a
    # single-issue classification the app inferred does not get to refuse
    # one. A classification the user set (`special_version_locked`) still
    # does -- that is an assertion, not an assumption.
    #
    # Nothing is claimed by letting the file through: `scan_files` files a
    # file naming an issue the volume does not have against the volume
    # rather than an issue, so the issues it does know about stay wanted.
    inferred_single_issue = (
        not getattr(volume_data, 'special_version_locked', False)
        and volume_data.special_version in SINGLE_ISSUE_VERSIONS
    )

    # A collected edition's `v02` is the collection's number and its year
    # is the collection's year; neither is the series'. Requiring them to
    # equal the volume's was a category error that refused
    # "Black Hammer Omnibus v02" from the Black Hammer folder it sits in,
    # and `scan_files` has had a branch waiting for exactly these files
    # that this gate never let it reach.
    collected = collected_edition_of_volume(file_data, volume_data)

    is_match = (
        (matching_special_version or inferred_single_issue)
        and (
            matching_volume_number
            or matching_year
            or collected
        )
    )

    return is_match


def download_group_filter(
    processed_desc: FilenameData,
    volume_data: VolumeData,
    ending_year: Union[int, None],
    volume_issues: List[IssueData]
) -> bool:
    """Filter for whether a download group is a match for the volume/issue.

    Args:
        processed_desc (FilenameData): Extracted data from group title.
        volume_data (VolumeData): The data of the volume.
        ending_year (Union[int, None]): The year of last issue or volume year.
        volume_issues (List[IssueData]): The data of the issues of the volume.

    Returns:
        bool: Whether the download group matches to the volume/issue or not.
    """
    annual = 'annual' in volume_data.title.lower()

    matching_title = match_title(
        volume_data.title,
        processed_desc['series']
    )

    matching_volume_number = match_volume_number(
        volume_data,
        volume_issues,
        processed_desc['volume_number'],
        conservative=True
    )

    matching_year = match_year(
        volume_data.year,
        processed_desc['year'],
        ending_year or volume_data.year,
        conservative=True
    )

    matching_special_version = match_special_version(
        volume_data.special_version.value,
        processed_desc['special_version'],
        volume_data.title,
        processed_desc['issue_number']
    )

    is_match = (
        matching_title
        and processed_desc['annual'] == annual
        and matching_special_version
        and matching_volume_number
        and matching_year
    )

    return is_match


def check_search_result_match(
    result: SearchResultData,
    volume_data: VolumeData,
    volume_issues: List[IssueData],
    number_to_year: Mapping[float, Union[int, None]],
    calculated_issue_number: Union[float, None] = None
) -> SearchResultMatchData:
    """Filter for whether a search result matches with what is searched for.

    Args:
        result (SearchResultData): A search result.

        volume_data (VolumeData): The data of the volume.

        volume_issues (List[IssueData]): The data of the issues of the volume.

        number_to_year (Mapping[float, Union[int, None]]): calculated issue
            numbers mapped to their release year for all issues of volume.

        calculated_issue_number (Union[float, None], optional): The calculated
            issue number of the issue, if the search was for an issue.
            Defaults to None.

    Returns:
        SearchResultMatchData: Whether the search result passes the filter.
    """
    annual = 'annual' in volume_data.title.lower()

    if blocklist_contains(result['link']):
        return {'match': False, 'match_issue': 'Link is blocklisted'}

    if result['annual'] != annual:
        return {'match': False, 'match_issue': 'Annual conflict'}

    if not (
        match_title(volume_data.title, result['series'])
        or match_title(volume_data.alt_title or '', result['series'])
    ):
        return {'match': False, 'match_issue': "Titles don't match"}

    if not match_volume_number(
        volume_data,
        volume_issues,
        result['volume_number'],
        conservative=True
    ):
        return {'match': False, 'match_issue': "Volume numbers don't match"}

    if not match_special_version(
        volume_data.special_version,
        result['special_version'],
        volume_data.title,
        result['issue_number']
    ):
        return {'match': False, 'match_issue': 'Special version conflict'}

    if result['issue_number'] is not None:
        issue_number = result['issue_number']

    elif (
        volume_data.special_version == SpecialVersion.VOLUME_AS_ISSUE
        and result['volume_number'] is not None
    ):
        issue_number = result['volume_number']

    else:
        issue_number = float('-inf')

    if not match_year(
        volume_data.year,
        result['year'],
        number_to_year.get(force_range(issue_number)[-1]),
        conservative=True
    ):
        return {'match': False, 'match_issue': "Year doesn't match"}

    if volume_data.special_version in (
        SpecialVersion.NORMAL,
        SpecialVersion.VOLUME_AS_ISSUE
    ):
        if calculated_issue_number is None:
            # Volume search
            if not all(
                i in number_to_year
                for i in force_range(issue_number)
            ):
                # One of the extracted issue numbers is not found in volume
                return {
                    'match': False,
                    'match_issue': "Issue numbers don't match"
                }

        elif issue_number != calculated_issue_number:
            # Issue search, but
            # extracted issue number(s) don't match number of searched issue
            return {'match': False, 'match_issue': "Issue numbers don't match"}

    return {'match': True, 'match_issue': None}


ONE_ISSUE_MATCH = (
    SpecialVersion.TPB,
    SpecialVersion.ONE_SHOT,
    SpecialVersion.HARD_COVER,
    SpecialVersion.OMNIBUS
)
"""
If a volume is one of these types, it can only match to search results
with one issue.
"""

COLLECTED_EDITION_MATCH = (
    SpecialVersion.TPB,
    SpecialVersion.HARD_COVER,
    SpecialVersion.OMNIBUS
)
"""
A file of one of these types collects a run of issues rather than being one
of them, so it can describe a volume of any length: it covers every issue.
A one-shot is deliberately absent -- it is a single standalone issue, not a
collection, so it still only matches a one-issue volume.
"""

# Continuous auto-import is intentionally stricter than the review scan. A
# candidate needs strong filename evidence and, when another viable candidate
# exists, a decisive lead over the runner-up before unattended import is safe.
AUTO_IMPORT_MIN_MATCH_SCORE = 4
AUTO_IMPORT_MIN_SCORE_MARGIN = 2


def _rank_volume_results_for_file(
    group: Dict[str, FilenameData],
    search_results: List[VolumeMetadata],
    only_english: bool
) -> List[Tuple[VolumeMetadata, int]]:
    """Return viable ComicVine matches ranked by filename evidence."""
    first_file = next(iter(group.values()))
    series = first_file['series']
    volume_number = first_file['volume_number']
    special_version = first_file['special_version']

    _years = [f['year'] for f in group.values() if f['year'] is not None]
    start_year = min(_years, default=None)
    end_year = max(_years, default=None)

    highest_issue_number = max(chain.from_iterable(
        force_range(f['issue_number'])
        for f in group.values()
        if f['issue_number'] is not None
    ), default=None)

    # Find out how many issues the files in the group AT LEAST cover. The issue
    # count is equal to or lower than the truth. If all issues have round issue
    # numbers, then the number is equal. But if a file covers a range like 3a-4b,
    # then without knowing what the issues of the volume exactly are (which we
    # don't) we won't know how many issues it covers. Is the last 3* issue b? c?
    # z? We consider such a range to cover AT LEAST 2 issues. This gives a
    # bottom limit to filter with.
    covered_issues = set()
    min_issue_count = 0
    for file in group.values():
        if file['issue_number'] is None:
            continue

        issue_range = force_range(file['issue_number'])

        if issue_range[0] in covered_issues:
            continue
        covered_issues.add(issue_range[0])

        min_issue_count += (
            floor(issue_range[1]) - floor(issue_range[0]) + 1
        )

    def viable(
        title_matches_series,
        exclude_translated: bool,
        enforce_issue_count: bool
    ) -> List[VolumeMetadata]:
        filtered_results: List[VolumeMetadata] = []
        for result in search_results:
            # Filter series titles
            if not title_matches_series(series, result['title']):
                continue

            # Filter non-english languages
            language_allowed = not (
                only_english and exclude_translated and result['translated']
            )
            if not language_allowed:
                continue

            # Filter based on SV
            # - Skip impossible SVs (e.g. 'one-shot' title vs 'hard-cover' file).
            regex_result = special_version_regex.search(result['title'])
            result_special_version = None
            if regex_result:
                result_special_version = [
                    k for k, v in regex_result.groupdict().items()
                    if v is not None
                ][0].replace('_', '-')

            special_version_possible = not (
                special_version in ONE_ISSUE_MATCH
                and result_special_version in ONE_ISSUE_MATCH
                and special_version != result_special_version
            )
            if not special_version_possible:
                continue

            # Filter based on issue count
            # - If the file is for a one-issue SV while the result has more than one
            #   issue, then it can't be a match.
            # - If miminum amount of issues covered by group is already more than
            #   result's issue count, then it can't be a match.
            sv_issue_count_allowed = (
                first_file['special_version'] not in ONE_ISSUE_MATCH
                or result['issue_count'] == 1
                # An omnibus collects a run instead of being one issue of it, so
                # the series it collects is exactly what it should match. Without
                # this, "Black Hammer Omnibus" could only ever match a one-issue
                # namesake, and the real Black Hammer was filtered out before
                # anything was scored.
                or first_file['special_version'] in COLLECTED_EDITION_MATCH
            )
            if not sv_issue_count_allowed:
                continue

            # A volume the user already added is not a coincidental namesake:
            # they have already decided it is the real series, and Kapowarr put
            # its files on disk. Its provider issue count is a claim about the
            # provider's records, and those records go stale -- ComicVine lists
            # two issues of "Death of Power" while #3, #4 and #5 sit in the
            # volume's own folder. Filtering the volume out on that count left
            # its own files with no candidate at all, so the folder was held as
            # 'no-candidate' forever with the answer already in the library.
            #
            # Scores still apply: an already-added volume that cannot hold the
            # issues is dispreferred by `rate_search_result` below, it is simply
            # no longer erased before anything can weigh it.
            already_in_library = result.get('already_added') is not None
            atleast_min_covered_issues = result['issue_count'] >= min_issue_count
            if enforce_issue_count and not (
                atleast_min_covered_issues or already_in_library
            ):
                continue

            # Search result passed the filters
            filtered_results.append(result)

        return filtered_results

    # Two of the hard gates that run before anything is scored are each
    # individually capable of erasing every candidate, leaving the folder
    # held as `no-candidate` -- "no database has this" -- with the right
    # volume sitting in the response that was just fetched.
    #
    # Neither is wrong as a preference. They are wrong as the last word.
    # A ladder of strictly widening passes keeps each one the preference
    # it should be: the first pass that admits anybody wins, so a folder
    # that matches under the strictest terms never sees a looser
    # candidate, and a folder that matched nothing gets the best evidence
    # available instead of nothing at all. The score, margin and tie rules
    # apply unchanged after that, so a relaxed candidate still has to earn
    # its import.
    #
    # Ordered by what the relaxation costs. A `translated` flag is the
    # cheaper of the two to give up: it is frequently just wrong, and it
    # was dropping "Astronaut Down" (2022) on an exact title and an exact
    # year. The title is the strongest signal there is, so it is relaxed
    # last, and never abandoned -- see `match_title_nearly`.
    #
    # The issue-count gate is deliberately not on this ladder. #146 opened
    # it for a volume the user already owns, on the grounds that a
    # provider's issue count is a claim about the provider's records
    # rather than about the series -- and closed it for everything else,
    # because a namesake the user has not vouched for and which cannot
    # hold the files is exactly what that gate is for. Widening it here
    # would quietly reverse that.
    for title_rule, exclude_translated in (
        (match_title, True),
        (match_title, False),
        (match_title_nearly, True),
        (match_title_nearly, False),
    ):
        filtered_results = viable(title_rule, exclude_translated, True)
        if filtered_results:
            break

    def rate_search_result(search_result: VolumeMetadata) -> int:
        rating = 0

        if search_result['year'] == start_year:
            # Years exactly match. Will also match fuzzy year.
            rating += 1

        if match_year(start_year, search_result['year'], end_year):
            # Years roughly match
            rating += 1

        if (
            volume_number is not None
            and search_result['volume_number'] == volume_number
        ):
            # Volume numbers match
            rating += 2

        if search_result['issue_count'] == min_issue_count:
            # Files cover exactly the issue count that the search result has.
            rating += 1

        if (
            highest_issue_number is not None
            and highest_issue_number > search_result['issue_count']
        ):
            # Disprefer because there's a file with an issue number that's
            # higher than the issue count of the search result. E.g. a file with
            # issue 6 but the search result only has 4 issues.
            rating -= ISSUE_CAPACITY_RATING_PENALTY

        return rating

    ranked_results = [
        (result, rate_search_result(result))
        for result in filtered_results
    ]
    ranked_results.sort(key=lambda item: item[1], reverse=True)
    return ranked_results


def select_best_volume_result_for_file(
    group: Dict[str, FilenameData],
    search_results: List[VolumeMetadata],
    only_english: bool
) -> Union[VolumeMetadata, None]:
    """Choose the best viable volume match for an interactive review scan."""
    ranked_results = _rank_volume_results_for_file(
        group,
        search_results,
        only_english
    )
    if not ranked_results:
        return None

    return ranked_results[0][0]


def select_confident_volume_result_for_file(
    group: Dict[str, FilenameData],
    search_results: List[VolumeMetadata],
    only_english: bool
) -> Union[VolumeMetadata, None]:
    """Choose a match only when unattended import has strong evidence.

    The best viable result must score at least 4 on the existing 0-5 filename
    evidence scale. When a runner-up exists, the winner must also lead it by at
    least two points. Ambiguous or weak matches return ``None`` so continuous
    import leaves the folder untouched for human review.
    """
    ranked_results = _rank_volume_results_for_file(
        group,
        search_results,
        only_english
    )
    if not ranked_results:
        return None

    best_result, best_score = ranked_results[0]
    if best_score < AUTO_IMPORT_MIN_MATCH_SCORE:
        return None

    if len(ranked_results) > 1:
        runner_up_score = ranked_results[1][1]
        if best_score - runner_up_score < AUTO_IMPORT_MIN_SCORE_MARGIN:
            return None

    return best_result
