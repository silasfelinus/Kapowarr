# -*- coding: utf-8 -*-

from asyncio import gather, run
from typing import Dict, List, Mapping, Sequence, Tuple, Type, Union

from backend.base.definitions import (DownloadType, MatchedSearchResultData,
                                      SearchResultData, SearchSource,
                                      SpecialVersion)
from backend.base.file_extraction import refine_special_version
from backend.base.helpers import (AsyncSession, check_overlapping_issues,
                                  extract_year_from_date, force_range,
                                  normalise_query_string)
from backend.base.logging import LOGGER
from backend.implementations.getcomics import search_getcomics
from backend.implementations.indexers import Indexers, search_indexer
from backend.implementations.matching import check_search_result_match
from backend.implementations.query_builders import QueryBuilders
from backend.implementations.torznab import (TorznabIndexers,
                                            search_torznab_indexer)
from backend.implementations.volumes import Volume


class SearchSources:
    """Registry of search-source implementations by acquisition protocol.

    Newznab remains backed by the fork's existing indexer table, GetComics is
    configuration-free, and Torznab/Prowlarr/Jackett register as torrent peers
    without teaching the coordinator source-specific branches.
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
    """Give a search result a rank, based on which you can sort."""
    rating = []
    rating.append(not result['match'])

    split_title = title.split(' ')
    rating.append(len([
        word
        for word in result['series'].split(' ')
        if word not in split_title
    ]))

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
        vy_score -= 2

    elif (
        year[0] is not None
        and year[1] is not None
        and result['year'] is not None
        and year[0] - 1 <= result['year'] <= year[1] + 1
    ):
        vy_score -= 1

    rating.append(vy_score)

    if calculated_issue_number is not None:
        if (
            isinstance(result['issue_number'], float)
            and calculated_issue_number == result['issue_number']
        ):
            rating.append(0)

        elif isinstance(result['issue_number'], tuple):
            if (
                result['issue_number'][0]
                <= calculated_issue_number
                <= result['issue_number'][1]
            ):
                rating.append(
                    1 - (1 / (
                        result['issue_number'][1] - result['issue_number'][0] + 1
                    ))
                )
            else:
                rating.append(3)

        elif result['special_version'] is not None:
            rating.append(2)
        else:
            rating.append(3)

    else:
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
            if result['link'] not in processed_links:
                search_results.append(result)
                processed_links.add(result['link'])

    return search_results


async def search_multiple_queries(*queries: str) -> List[SearchResultData]:
    """Search every registered source with the same query variations."""
    async with AsyncSession() as session:
        searches = [
            Source(query).search(session)
            for sources in SearchSources.sources.values()
            for Source in sources
            for query in queries
        ]
        responses = await gather(*searches)

    return _dedupe_search_results(responses)


async def search_planned_queries(
    query_plan: Mapping[DownloadType, Sequence[str]]
) -> List[SearchResultData]:
    """Search each protocol only with the queries built for that protocol."""
    async with AsyncSession() as session:
        searches = [
            Source(query).search(session)
            for download_type, queries in query_plan.items()
            for Source in SearchSources.sources.get(download_type, [])
            for query in queries
        ]
        responses = await gather(*searches)

    return _dedupe_search_results(responses)


def manual_search(
    volume_id: int,
    issue_id: Union[int, None] = None
) -> List[MatchedSearchResultData]:
    """Do a manual search for a volume or issue."""
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
                **check_search_result_match(
                    result, volume_data, volume_issues,
                    number_to_year, calculated_issue_number
                )
            }
            for result in search_results
        ]

        search_title = normalise_query_string(title).replace(':', '')
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
    """Search for a volume or issue and automatically choose a result."""
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
        pass

    elif issue_id is None:
        searchable_issues = volume.get_open_issues()

    else:
        issue = volume.get_issue(issue_id)
        issue_data = issue.get_data()
        if issue_data.monitored and not issue.get_files():
            searchable_issues = [(issue_id, issue_data.calculated_issue_number)]

    if not searchable_issues:
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
        result = search_results[:1] if search_results else []
        LOGGER.debug('Auto search results: %s', result)
        return result

    chosen_downloads: List[MatchedSearchResultData] = []
    searchable_issue_numbers = {i[1] for i in searchable_issues}
    for result in search_results:
        result = refine_special_version(volume_data, result)

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
            continue

        for part in chosen_downloads:
            if check_overlapping_issues(
                part["issue_number"], # type: ignore
                result["issue_number"]
            ):
                break
        else:
            chosen_downloads.append(result)

    missing_issues = [
        i
        for i in searchable_issues
        if not any(
            check_overlapping_issues(
                i[1], part["issue_number"] # type: ignore
            )
            for part in chosen_downloads
        )
    ]

    for missing_issue in missing_issues:
        chosen_downloads.extend(auto_search(volume_id, missing_issue[0]))

    LOGGER.debug('Auto search results: %s', chosen_downloads)
    return chosen_downloads
