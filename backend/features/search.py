# -*- coding: utf-8 -*-

from asyncio import gather, run
from time import monotonic
from typing import (Callable, Dict, List, Mapping, NamedTuple, Optional,
                    Sequence, Tuple, Type, Union)

from backend.base.definitions import (DownloadType, MatchedSearchResultData,
                                      SearchResultData, SearchResultMatchData,
                                      SearchSource, SpecialVersion)
from backend.base.file_extraction import refine_special_version
from backend.base.helpers import (AsyncSession, check_overlapping_issues,
                                  extract_year_from_date, force_range,
                                  normalise_query_string)
from backend.base.logging import LOGGER
from backend.features.acquisition_preferences import (
    availability_rank, indexer_priority, ordered_download_types,
    pack_preference_rank, search_stops_at_first_match)
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


class SearchResultRank(NamedTuple):
    """The ranking tiers _rank_search_result() computes, most significant
    first. This states the ranking policy in one place, by name, instead of
    positionally in a bare list -- see RANKING_TIERS in
    tests/Tbackend/acquisition_preferences.py for the full order and why it
    matters.

    Tuples compare lexicographically exactly like the List[int] this
    replaces, so every sort/comparison call site is unaffected.

    `issue_fit` defaults to 0: the volume-search branch (calculated_issue_number
    is None) leaves it at that default when a result's issue_number is neither
    a tuple nor a float, which is the one case that used to append nothing at
    all. 0 is strictly less than every value the volume-search branch computes
    otherwise -- (0, 1] -- so this preserves the old comparison exactly (a
    shorter list sorts before a longer one it's a prefix of).
    """
    match: Union[int, float]
    title_overlap: Union[int, float]
    volume_year: Union[int, float]
    availability: Union[int, float]
    pack_preference: Union[int, float]
    issue_fit: Union[int, float] = 0


def _rank_search_result(
    result: MatchedSearchResultData,
    title: str,
    volume_number: int,
    year: Tuple[Union[int, None], Union[int, None]] = (None, None),
    calculated_issue_number: Union[float, None] = None
) -> SearchResultRank:
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
        SearchResultRank: The tiered ranking of the result.
    """
    # Prefer matches (False == 0 == higher rank)
    match = not result['match']

    # The more words in the search term that are present in
    # the search results' title, the higher ranked it gets
    split_title = title.split(' ')
    title_overlap = len([
        word
        for word in result['series'].split(' ')
        if word not in split_title
    ])

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

    volume_year = vy_score

    # Peer availability sits below match/title/year correctness -- a well-seeded
    # wrong issue is still the wrong issue -- but above pack preference, because
    # preferring the shape of a release nobody can download is meaningless.
    # Only an explicit zero-seeder count is demoted; see availability_rank().
    availability = availability_rank(result)

    # User pack preference is deliberately below match/title/year correctness,
    # but above the historical issue-shape tie-breaker. Neutral adds the same
    # zero to every result and therefore preserves the old order exactly.
    pack_preference = pack_preference_rank(result['issue_number'])

    # Sort on issue number fitting. Left at the class default (0) when the
    # volume-search branch below has nothing to say about it.
    issue_fit: Union[int, float] = 0
    if calculated_issue_number is not None:
        # Search was for issue
        if (
            isinstance(result['issue_number'], float)
            and calculated_issue_number == result['issue_number']
        ):
            # Issue number is direct match
            issue_fit = 0

        elif isinstance(result['issue_number'], tuple):
            if (
                result['issue_number'][0]
                <= calculated_issue_number
                <= result['issue_number'][1]
            ):
                # Issue number falls between range
                issue_fit = 1 - (1 / (
                    result['issue_number'][1] - result['issue_number'][0] + 1
                ))

            else:
                # Issue number falls outside so release is not useful
                issue_fit = 3

        elif result['special_version'] is not None:
            # Issue number not found but is special version
            issue_fit = 2

        else:
            # No issue number found and not special version
            issue_fit = 3

    else:
        # Search was for volume
        if isinstance(result['issue_number'], tuple):
            issue_fit = (
                1.0
                /
                (result['issue_number'][1] - result['issue_number'][0] + 1)
            )

        elif isinstance(result['issue_number'], float):
            issue_fit = 1

    return SearchResultRank(
        match, title_overlap, volume_year, availability, pack_preference,
        issue_fit
    )


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
        indexers.sort(key=lambda indexer: indexer_priority('newznab', indexer.id))

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
        indexers.sort(key=lambda indexer: indexer_priority('torznab', indexer.id))

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


def _probe_order(queries: Sequence[str]) -> List[str]:
    """Broadest query first, then the rest in their original order.

    The builders phrase one search several ways, from most specific to just
    the series title, and every specific phrasing is the broad one plus extra
    terms. Newznab and Torznab AND the terms in `q`, so the broad query
    returns a superset of what any of the others would: asking all of them
    spends a request per phrasing to receive results already contained in the
    first.

    Order alone does not save anything -- :func:`_search_source` is what stops
    early -- but it decides which single query is usually the only one sent.
    """
    if len(queries) < 2:
        return list(queries)

    broadest = min(queries, key=lambda query: len(query.split()))
    return [broadest] + [query for query in queries if query != broadest]


async def _search_source(Source, queries: Sequence[str], session):
    """Ask one source, widening only if the broad query found nothing.

    Stopping at the first query that returns anything turns a search from one
    request per phrasing into one request, in the case where the broad query
    works -- which is the usual case, since it is the least constrained. The
    remaining phrasings are still tried when it comes back empty: an indexer
    caps how many results it will return, so a broad query against a prolific
    title can push the wanted release off the end of the list where a more
    specific phrasing would surface it.
    """
    for query in _probe_order(queries):
        results = await Source(query).search(session)
        if results:
            return results

    return []


async def search_planned_queries(
    query_plan: Mapping[DownloadType, Sequence[str]],
    accepts: Optional[Callable[[List[SearchResultData]], bool]] = None
) -> List[SearchResultData]:
    """Search each protocol only with the queries built for that protocol.

    Protocol preference changes the stable input order, so otherwise-equal
    results inherit the user's preferred acquisition source without making
    source choice more important than matching correctness.

    Sources are still searched concurrently; the phrasings within one source
    are not, because each is only sent when the one before it found nothing.

    `accepts` turns the preference from an ordering into a decision. Given
    one, protocols are searched a tier at a time in preference order and the
    first tier whose results it accepts ends the search -- so a protocol
    further down the list is never asked when a preferred one already had
    the issue. That matters when the protocols are not equally cheap: three
    pro Usenet accounts allow around ten thousand queries a day between
    them, three public torrent indexers a hundred, and under the concurrent
    search every issue spends the scarce one whether the plentiful one
    answered or not.

    It asks whether the results *match*, not whether there are any. An
    indexer will happily return fifty rows for a title it does not have, and
    stopping on those would be worse than not gating at all.
    """
    if accepts is None:
        async with AsyncSession() as session:
            searches = [
                _search_source(Source, query_plan[download_type], session)
                for download_type in ordered_download_types(tuple(query_plan))
                for Source in SearchSources.sources.get(download_type, [])
            ]
            responses = await gather(*searches)

        return _dedupe_search_results(responses)

    gathered: List[List[SearchResultData]] = []
    async with AsyncSession() as session:
        for download_type in ordered_download_types(tuple(query_plan)):
            sources = SearchSources.sources.get(download_type, [])
            if not sources:
                continue

            responses = await gather(*(
                _search_source(Source, query_plan[download_type], session)
                for Source in sources
            ))
            gathered.extend(responses)

            results = _dedupe_search_results(gathered)
            if accepts(results):
                LOGGER.debug(
                    'Stopping at %s: it has a match, so no lower-preference '
                    'protocol is asked', download_type.name
                )
                return results

    # Nobody matched. Everything gathered is returned regardless, so the
    # caller ranks over the same pool it would have had anyway.
    return _dedupe_search_results(gathered)


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
    started = monotonic()

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

        def matched(result):
            return {
                **result,
                **_match_search_result(
                    result, volume_data, volume_issues,
                    number_to_year, calculated_issue_number
                )
            }

        # Under `first_match` the preference decides who gets asked, so the
        # question a tier has to answer is whether it found this issue --
        # not whether it returned rows. An indexer returns fifty rows for a
        # title it does not carry, and stopping on those would spend the
        # preferred protocol's answer on nothing while never asking the
        # protocol that had it.
        accepts = None
        if search_stops_at_first_match():
            def accepts(results):
                return any(matched(result)['match'] for result in results)

        search_results = run(search_planned_queries(query_plan, accepts))
        if not search_results:
            continue

        results: List[MatchedSearchResultData] = [
            matched(result) for result in search_results
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
        _log_search_cost(volume_data, issue_number, started, len(results))
        return results

    _log_search_cost(volume_data, issue_number, started, 0)
    return []


def _log_search_cost(volume_data, issue_number, started, found: int) -> None:
    """Say what a search cost, because nothing did.

    A sweep over thousands of volumes is only as good as the time one
    search takes, and that number appeared nowhere: the log said a search
    started and then said the next one started, and the difference had to
    be worked out by subtracting timestamps by hand. Silas's 2026-09-01
    sweep was spending 110 seconds per search -- which at that library size
    is months for one pass -- and the only way to see it was to notice the
    gaps.

    Configured indexer delays are the usual reason. A protocol's delay is
    charged per request, and one search sends up to one request per query
    phrasing per indexer, so the delay multiplies by however many
    phrasings a fruitless search works through.
    """
    elapsed = monotonic() - started
    LOGGER.info(
        'Search finished in %.1fs: %s (%s) %s -- %d result(s)',
        elapsed, volume_data.title, volume_data.year,
        f'#{issue_number}' if issue_number else '',
        found
    )


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