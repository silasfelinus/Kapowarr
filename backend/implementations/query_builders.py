# -*- coding: utf-8 -*-

"""Protocol-specific comic search query builders.

This is a selective port of upstream Kapowarr's query-builder seam. The fork
already has working GetComics and Newznab clients, so the useful part to import
is the separation between *what metadata we are searching for* and *how a
protocol phrases that search*. Torznab can register its own builder next
without adding another branch to ``features.search.manual_search``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Type

from backend.base.definitions import DownloadType, SpecialVersion, VolumeData
from backend.base.helpers import normalise_query_string


class QueryBuilder(ABC):
    """Build ordered query variations for one acquisition protocol."""

    @abstractmethod
    def build(
        self,
        volume: VolumeData,
        title: str,
        issue_number: str = None
    ) -> List[str]:
        ...


class QueryBuilders:
    """Registry mapping acquisition protocol to its query builder."""

    builders: Dict[DownloadType, Type[QueryBuilder]] = {}

    @classmethod
    def register(cls, *download_types: DownloadType):
        def wrapper(builder: Type[QueryBuilder]) -> Type[QueryBuilder]:
            for download_type in download_types:
                if download_type in cls.builders:
                    raise RuntimeError(
                        'Query builder registered multiple times for '
                        f'{download_type.name}'
                    )
                cls.builders[download_type] = builder
            return builder
        return wrapper

    @classmethod
    def get(cls, download_type: DownloadType) -> QueryBuilder:
        return cls.builders[download_type]()


@QueryBuilders.register(DownloadType.DIRECT, DownloadType.USENET)
class ComicQueryBuilder(QueryBuilder):
    """Current GetComics/Newznab query policy, isolated behind a protocol seam.

    Keeping these two protocols identical today is intentional. The separation
    means torrent/Torznab can diverge later without changing search orchestration.
    """

    TPB_FORMATS = (
        '{title} Vol. {volume_number} ({year}) TPB',
        '{title} ({year}) TPB',
        '{title} Vol. {volume_number} TPB',
        '{title} Vol. {volume_number}',
        '{title}'
    )
    VAI_FORMATS = (
        '{title} ({year})',
        '{title}'
    )
    VOLUME_FORMATS = (
        '{title} Vol. {volume_number} ({year})',
        '{title} ({year})',
        '{title} Vol. {volume_number}',
        '{title}'
    )
    ISSUE_FORMATS = (
        '{title} #{issue_number} ({year})',
        '{title} Vol. {volume_number} #{issue_number}',
        '{title} #{issue_number}',
        '{title}'
    )

    def build(
        self,
        volume: VolumeData,
        title: str,
        issue_number: str = None
    ) -> List[str]:
        if volume.special_version == SpecialVersion.TPB:
            formats = self.TPB_FORMATS
        elif volume.special_version == SpecialVersion.VOLUME_AS_ISSUE:
            formats = self.VAI_FORMATS
        elif issue_number is None:
            formats = self.VOLUME_FORMATS
        else:
            formats = self.ISSUE_FORMATS

        search_title = normalise_query_string(title).replace(':', '')
        queries = []
        for query_format in formats:
            if volume.year is None:
                query_format = query_format.replace('({year})', '').strip()

            query = query_format.format(
                title=search_title,
                volume_number=volume.volume_number,
                year=volume.year,
                issue_number=issue_number
            )
            # Year-less formatting can leave repeated spaces behind.
            query = ' '.join(query.split())
            if query not in queries:
                queries.append(query)

        return queries
