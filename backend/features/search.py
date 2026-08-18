# -*- coding: utf-8 -*-

from asyncio import gather, run
from typing import Dict, List, Mapping, Sequence, Tuple, Type, Union

from backend.base.definitions import (DownloadType, MatchedSearchResultData,
                                      SearchResultData, SearchResultMatchData,
                                      SearchSource, SpecialVersion)
from backend.base.file_extraction import refine_special_version
from backend.base.helpers import (AsyncSession, check_overlapping_issues,
                                  extract_year_from_date, force_range,
                                  normalise_query_string)
from backend.base.logging import LOGGER
from backend.features.acquisition_preferences import (ordered_download_types,
                                                       pack_preference_rank)
from backend.implementations.getcomics import search_getcomics
from backend.implementations.indexers import Indexers, search_indexer
from backend.implementations.matching import check_search_result_match
from backend.implementations.query_builders import QueryBuilders
from backend.implementations.torznab import (TorznabIndexers,
                                            search_torznab_indexer)
from backend.implementations.volumes import Volume


class SearchSources:
    """Registry of search-source implementations by acquisition protocol.

    This is the small, fork-compatible part of upstream's indexer-client
    manager that we actually need. Newznab remains backed by the fork's existing
    indexer table and GetComics remains configuration-free; Torznab can register
    as a torrent peer without teaching the search coordinator another special
    case.
    """

    sources: Dict[DownloadType, List[Type[SearchSource]]] = {
        download_type: []
        for download_type in DownloadType
    }

    @classmethod
    def register(cls, download_type: DownloadType):
        def wrapper(source: Type[SearchSource]) -> Type[SearchSource]:
            cls.sources[download_type].append(source)
            return source
        return wrapper

    @classmethod
    def active_types(cls) -> List[DownloadType]:
        return [
            download_type
            for download_type, sources in cls.sources.items()
            if sources
        ]


def _rank_search_result(
    result: MatchedSearchResultData,
    title: str,
    volume_number: int,
    year: Tuple[Union[int, None], Union[int, None]] = (None, None),
    calculated_issue_number: Union[float, None] = None
) -> List[int]:
    """Give a search result a rank, based on which you can sort.

    Args:
        result (MatchedSearchResultData): A search result.

        title (str): Title of volume.

        volume_number (int): The volume number of the volume.

        year (Tuple[Union[int, None], Union[int, None]], optional): The year of
        the volume and the year of the issue if searching for an issue and
        release date is known.
            Defaults to (None, None).

        calculated_issue_number (Union[float, None], optional): The
        calculated_issue_number of the issue.
            Defaults to None.

    Returns:
        List[int]: A list of numbers which determines the ranking of the result.
    """
    rating = []

    # Prefer matches (False == 0 == higher rank)
    rating.append(not result['match'])

    # The more words in the search term that are present in
    # the search results' title, the higher ranked it gets
    split_title = title.split(' ')
    rating.append(len([
        word
        for word in result['series'].split(' ')
        if word not in split_title
    ]))

    # Prefer volume number or year matches, even better if both match
    vy_score = 3
    if (
        result['volume_number'] is not None
        and result['volume_number'] == volume_number
    ):
        vy_score -= 1

    if (
        year[1] is not None
        and result['year'] is not None
        and year[1] == result['year']
    ):
        # issue year direct match
        vy_score -= 2

    elif (
        year[0] is not None
        and year[1] is not None
        and result['year'] is not None
        and year[0] - 1 <= result['year'] <= year[1] + 1
    ):
        # fuzzy match between start year and issue year
        vy_score -= 1

    rating.append(vy_score)

    # User pack preference is deliberately below match/title/year correctness,
    # but above the historical issue-shape tie-breaker. Neutral adds the same
    # zero to every result and therefore preserves the old order exactly.
    rating.append(pack_preference_rank(result['issue_number']))

    # Sort on issue number fitting
    if calculated_issue_number is not None:
        # Search was for issue
        if (
            isinstance(result['issue_number'], float)
            and calculated_issue_number == result['issue_number']
        ):
            # Issue number is direct match
            rating.append(0)

        elif isinstance(result['issue_number'], tuple):
            if (
                result['issue_number'][0]
                <= calculated_issue_number
                <= result['issue_number'][1]
            ):
                # Issue number falls between range
                rating.append(
                    1 - (1 / (
                        result['issue_number'][1] - result['issue_number'][0] + 1
                    ))
                )

            else:
                # Issue number falls outside so release is not useful
                rating.append(3)

        elif result['special_version'] is not None:
            # Issue number not found but is special version
            rating.append(2)

        else:
            # No issue number found and not special version
            rating.append(3)

    else:
        # Search was for volume
        if isinstance(result['issue_number'], tuple):
            rating.append(
                1.0
                /
                (result['issue_number'][1] - result['issue_number'][0] + 1)
            )

        elif isinstance(result['issue_number'], float):
            rating.append(1)

    return rating


@SearchSources.register(DownloadType.DIRECT)
class SearchGetComics(SearchSource):
    async def search(self, session: AsyncSession) -> List[SearchResultData]:
        return await search_getcomics(session, self.query)


@SearchSources.register(DownloadType.USENET)
class SearchIndexers(SearchSource):
    async def search(self, session: AsyncSession) -> List[SearchResultData]:
        indexers = Indexers.get_enabled()
        if not indexers:
            return []

        responses = await gather(*(
            search_indexer(session, indexer, self.query)
            for indexer in indexers
        ))
        return [result for response in responses for result in response]


@SearchSources.register(DownloadType.TORRENT)
class SearchTorznab(SearchSource):
    async def search(self, session: AsyncSession) -> List[SearchResultData]:
        indexers = TorznabIndexers.get_enabled()
        if not indexers:
            return []

        responses = await gather(*(
            search_torznab_indexer(session, indexer, self.query)
            for indexer in indexers
        ))
        return [result for response in responses for result in response]


def _dedupe_search_results(
    responses: Sequence[List[SearchResultData]]
) -> List[SearchResultData]:
    search_results: List[SearchResultData] = []
    processed_links = set()
    for response in responses:
        for result in response:
            # Don't add if the link is already in the results. A source can
            # return the same release for multiple query variations.
            if result['link'] not in processed_links:
                search_results.append(result)
                processed_links.add(result['link'])

    return search_results


async def search_multiple_queries(*queries: str) -> List[SearchResultData]:
    """Search every registered source with the same query variations.

    Kept as a compatibility helper for callers/tests that already have query
    strings. New volume/issue searches use :func:`search_planned_queries` so
    each protocol can own its query-builder policy.
    """
    async with AsyncSession() as session:
        searches = [
            Source(query).search(session)
            for download_type in ordered_download_types(SearchSources.active_types())
            for Source in SearchSources.sources[download_type]
            for query in queries
        ]
        responses = await gather(*searches)

    return _dedupe_search_results(responses)


async def search_planned_queries(
    query_plan: Mapping[DownloadType, Sequence[str]]
) -> List[SearchResultData]:
    """Search each protocol only with the queries built for that protocol.

    Protocol preference changes the stable input order, so otherwise-equal
    results inherit the user's preferred acquisition source without making
    source choice more important than matching correctness.
    """
    async with AsyncSession() as session:
        searches = [
            Source(query).search(session)
            for download_type in ordered_download_types(tuple(query_plan))
            for Source in SearchSources.sources.get(download_type, [])
            for query in query_plan[download_type]
        ]
        responses = await gather(*searches)

    return _dedupe_search_results(responses)


def _match_search_result(
    result: SearchResultData,
    volume_data,
    volume_issues,
    number_to_year,
    calculated_issue_number: Union[float, None]
) -> SearchResultMatchData:
    """Match a result while allowing issue searches to use covering ranges.

    The shared matcher historically compares the extracted issue value directly
    to the requested issue, so ``1-100`` fails an issue-37 search even though it
    contains issue 37. Re-check only that range case using the requested issue
    number for validation, while leaving the result's original range untouched
    for queueing and pack normalization.
    """
    match = check_search_result_match(
        result,
        volume_data,
        volume_issues,
        number_to_year,
        calculated_issue_number
    )
    if match['match'] or calculated_issue_number is None:
        return match

    issue_number = result['issue_number']
    if not (
        isinstance(issue_number, tuple)
        and issue_number[0] <= calculated_issue_number <= issue_number[1]
    ):
        return match

    adjusted_result = {
        **result,
        'issue_number': calculated_issue_number
    }
    adjusted_match = check_search_result_match(
        adjusted_result,
        volume_data,
        volume_issues,
        number_to_year,
        calculated_issue_number
    )
    if adjusted_match['match']:
        return {'match': True, 'match_issue': None}
    return match


def manual_search(
    volume_id: int,
    issue_id: Union[int, None] = None
) -> List[MatchedSearchResultData]:
    """Do a manual search for a volume or issue.

    Args:
        volume_id (int): The id of the volume to search for.
        issue_id (Union[int, None], optional): The id of the issue to search for,
        in the case that you want to search for an issue instead of a volume.
            Defaults to None.

    Returns:
        List[MatchedSearchResultData]: List with search results.
    """
    volume = Volume(volume_id)
    volume_data = volume.get_data()
    volume_issues = volume.get_issues()
    number_to_year: Dict[float, Union[int, None]] = {
        i.calculated_issue_number: extract_year_from_date(i.date)
        for i in volume_issues
    }
    issue_number: Union[str, None] = None
    calculated_issue_number: Union[float, None] = None

    if issue_id and volume_data.special_version in (
        SpecialVersion.NORMAL,
        SpecialVersion.VOLUME_AS_ISSUE
    ):
        issue_data = volume.get_issue(issue_id).get_data()
        issue_number = issue_data.issue_number
        calculated_issue_number = issue_data.calculated_issue_number

    LOGGER.info(
        'Starting manual search: %s (%d) %s',
        volume_data.title, volume_data.year,
        f'#{issue_number}' if issue_number else ''
    )

    for title in (volume_data.title, volume_data.alt_title):
        if not title:
            continue

        query_plan: Dict[DownloadType, Sequence[str]] = {}
        for download_type in SearchSources.active_types():
            try:
                builder = QueryBuilders.get(download_type)
            except KeyError:
                LOGGER.warning(
                    'Search source registered without query builder: %s',
                    download_type.name
                )
                continue

            query_plan[download_type] = builder.build(
                volume_data,
                title,
                issue_number
            )

        search_results = run(search_planned_queries(query_plan))
        if not search_results:
            continue

        results: List[MatchedSearchResultData] = [
            {
                **result,
                **_match_search_result(
                    result, volume_data, volume_issues,
                    number_to_year, calculated_issue_number
                )
            }
            for result in search_results
        ]

        search_title = normalise_query_string(title).replace(':', '')
        # Sort results; put best result at top
        results.sort(key=lambda r: _rank_search_result(
            r, search_title, volume_data.volume_number,
            (
                volume_data.year,
                number_to_year.get(calculated_issue_number) # type: ignore
            ),
            calculated_issue_number
        ))

        LOGGER.debug('Manual search results: %s', results)
        return results

    return []


def auto_search(
    volume_id: int,
    issue_id: Union[int, None] = None
) -> List[MatchedSearchResultData]:
    """Search for a volume or issue and automatically choose a result.

    Args:
        volume_id (int): The ID of the volume to search for.
        issue_id (Union[int, None], optional): The id of the issue to search for,
        in the case that you want to search for an issue instead of a volume.
            Defaults to None.

    Returns:
        List[MatchedSearchResultData]: List with chosen search results.
    """
    volume = Volume(volume_id)
    volume_data = volume.get_data()
    volume_issues = volume.get_issues(_skip_files=True)
    volume_issues.sort(key=lambda i: i.calculated_issue_number)
    LOGGER.info(
        'Starting auto search for volume %d %s',
        volume_id,
        f'issue {issue_id}' if issue_id else ''
    )

    searchable_issues: List[Tuple[int, float]] = []
    if not volume_data.monitored:
        # Volume is unmonitored so don't auto search
        pass

    elif issue_id is None:
        # Auto search volume
        # Get open issues (monitored and no file).
        searchable_issues = volume.get_open_issues()

    else:
        # Auto search issue
        issue = volume.get_issue(issue_id)
        issue_data = issue.get_data()
        if issue_data.monitored and not issue.get_files():
            # Issue is open
            searchable_issues = [(issue_id, issue_data.calculated_issue_number)]

    if not searchable_issues:
        # No issues to search for
        result = []
        LOGGER.debug(f'Auto search results: {result}')
        return result

    search_results = [
        r
        for r in manual_search(volume_id, issue_id)
        if r['match']
    ]

    if issue_id is not None or volume_data.special_version not in (
        SpecialVersion.NORMAL,
        SpecialVersion.VOLUME_AS_ISSUE
    ):
        # We're searching for one "item", so just grab first search result.
        result = search_results[:1] if search_results else []
        LOGGER.debug('Auto search results: %s', result)
        return result

    # We're searching for a volume, so we might download multiple search results.
    # Find a combination of search results that download the most issues.
    chosen_downloads: List[MatchedSearchResultData] = []
    searchable_issue_numbers = {i[1] for i in searchable_issues}
    for result in search_results:
        result = refine_special_version(volume_data, result)

        # Determine what issues the result covers
        if result["special_version"]:
            result["issue_number"] = 1.0
            covered_issues = volume_issues

        elif result["issue_number"] is not None:
            if isinstance(result["issue_number"], tuple):
                n_start, n_end = result["issue_number"]
            else:
                n_start, n_end = force_range(result["issue_number"])

            covered_issues = [
                issue
                for issue in volume_issues
                if n_start <= issue.calculated_issue_number <= n_end
            ]

        else:
            continue

        if any(
            i.calculated_issue_number not in searchable_issue_numbers
            for i in covered_issues
        ):
            # Part or all of what the result covers is already downloaded.
            # Leave oversized packs to the issue-specific fallback below; it
            # can now accept a range containing a missing issue, while avoiding
            # giant packs when a clean volume-level combination is available.
            continue

        # Check that any other selected download doesn't already cover the issue
        for part in chosen_downloads:
            if check_overlapping_issues(
                part["issue_number"], # type: ignore
                result["issue_number"]
            ):
                break
        else:
            chosen_downloads.append(result)

    # Find issues that have still not been covered. A range found by one issue
    # search can satisfy several missing issues, so consume that coverage and do
    # not enqueue the same pack once per missing issue.
    remaining_missing = [
        i
        for i in searchable_issues
        if not any(
            check_overlapping_issues(
                i[1], part["issue_number"] # type: ignore
            )
            for part in chosen_downloads
        )
    ]
    chosen_links = {part['link'] for part in chosen_downloads}

    while remaining_missing:
        missing_issue = remaining_missing.pop(0)
        fallback_results = auto_search(volume_id, missing_issue[0])
        for fallback in fallback_results:
            if fallback['link'] not in chosen_links:
                chosen_downloads.append(fallback)
                chosen_links.add(fallback['link'])

            coverage = fallback.get('issue_number')
            if coverage is not None:
                remaining_missing = [
                    issue
                    for issue in remaining_missing
                    if not check_overlapping_issues(issue[1], coverage)
                ]

    LOGGER.debug('Auto search results: %s', chosen_downloads)
    return chosen_downloads