# -*- coding: utf-8 -*-

"""Source-specific preparation of search-result links for the download queue.

This selectively ports upstream Kapowarr's download-prepper seam while keeping
this fork's working GetComics and Newznab/SAB implementations intact. A prepper
owns the source-specific work required to turn one result link into one or more
``Download`` objects. The queue only needs to ask the registry which prepper
recognises a link.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Type, Union

from backend.base.custom_exceptions import EnqueuingDownloadFailure
from backend.base.definitions import (BlocklistReason, Constants, Download,
                                      EnqueuingDownloadFailureReason)
from backend.base.logging import LOGGER
from backend.implementations.blocklist import add_to_blocklist
from backend.implementations.getcomics import GetComicsPage
from backend.implementations.indexers import Indexers, create_nzb_download
from backend.implementations.torznab import (create_torznab_download,
                                            is_torznab_link)


class DownloadPrepper(ABC):
    """Convert one source result link into queue-ready download instances."""

    identifier: str

    @classmethod
    @abstractmethod
    def matches(cls, link: str) -> bool:
        """Return whether this prepper owns ``link``."""
        ...

    @classmethod
    @abstractmethod
    async def prepare(
        cls,
        link: str,
        volume_id: int,
        issue_id: Union[int, None] = None,
        force_match: bool = False
    ) -> List[Download]:
        """Turn ``link`` into one or more downloads or raise a known failure."""
        ...


class DownloadPreppers:
    """Registry of source-specific queue handoff implementations."""

    preppers: Dict[str, Type[DownloadPrepper]] = {}

    @classmethod
    def register(cls, identifier: str):
        def wrapper(prepper: Type[DownloadPrepper]) -> Type[DownloadPrepper]:
            if identifier in cls.preppers:
                raise RuntimeError(
                    f'Download prepper registered multiple times: {identifier}'
                )
            prepper.identifier = identifier
            cls.preppers[identifier] = prepper
            return prepper
        return wrapper

    @classmethod
    def get(cls, identifier: str) -> Type[DownloadPrepper]:
        return cls.preppers[identifier]

    @classmethod
    def get_for_link(cls, link: str) -> Union[Type[DownloadPrepper], None]:
        for prepper in cls.preppers.values():
            if prepper.matches(link):
                return prepper
        return None


@DownloadPreppers.register('gc')
class GetComicsDownloadPrepper(DownloadPrepper):
    @classmethod
    def matches(cls, link: str) -> bool:
        return link.startswith(Constants.GC_SITE_URL)

    @classmethod
    async def prepare(
        cls,
        link: str,
        volume_id: int,
        issue_id: Union[int, None] = None,
        force_match: bool = False
    ) -> List[Download]:
        page = GetComicsPage(link)
        try:
            await page.load_data()
        except EnqueuingDownloadFailure as error:
            add_to_blocklist(
                web_link=link,
                web_title=None,
                web_sub_title=None,
                download_link=None,
                source=None,
                volume_id=volume_id,
                issue_id=issue_id,
                reason=BlocklistReason.LINK_BROKEN
            )
            LOGGER.warning(
                'Unable to extract download links from source; fail_reason="%s"',
                error.reason.value
            )
            raise

        try:
            return await page.create_downloads(
                volume_id,
                issue_id,
                force_match
            )
        except EnqueuingDownloadFailure as error:
            if error.reason == EnqueuingDownloadFailureReason.NO_WORKING_LINKS:
                add_to_blocklist(
                    web_link=link,
                    web_title=page.title,
                    web_sub_title=None,
                    download_link=None,
                    source=None,
                    volume_id=volume_id,
                    issue_id=issue_id,
                    reason=BlocklistReason.NO_WORKING_LINKS
                )

            LOGGER.warning(
                'Unable to extract download links from source; fail_reason="%s"',
                error.reason.value
            )
            raise


@DownloadPreppers.register('nzb')
class NewznabDownloadPrepper(DownloadPrepper):
    @classmethod
    def matches(cls, link: str) -> bool:
        return Indexers.find_by_link(link) is not None

    @classmethod
    async def prepare(
        cls,
        link: str,
        volume_id: int,
        issue_id: Union[int, None] = None,
        force_match: bool = False
    ) -> List[Download]:
        try:
            return [await create_nzb_download(
                link,
                volume_id,
                issue_id,
                force_match
            )]
        except EnqueuingDownloadFailure as error:
            if error.reason == EnqueuingDownloadFailureReason.LINK_BROKEN:
                add_to_blocklist(
                    web_link=None,
                    web_title=None,
                    web_sub_title=None,
                    download_link=link,
                    source=None,
                    volume_id=volume_id,
                    issue_id=issue_id,
                    reason=BlocklistReason.LINK_BROKEN
                )

            LOGGER.warning(
                'Unable to add indexer download; fail_reason="%s"',
                error.reason.value
            )
            raise


@DownloadPreppers.register('torznab')
class TorznabDownloadPrepper(DownloadPrepper):
    @classmethod
    def matches(cls, link: str) -> bool:
        return is_torznab_link(link)

    @classmethod
    async def prepare(
        cls,
        link: str,
        volume_id: int,
        issue_id: Union[int, None] = None,
        force_match: bool = False
    ) -> List[Download]:
        try:
            return [await create_torznab_download(
                link,
                volume_id,
                issue_id,
                force_match
            )]
        except EnqueuingDownloadFailure as error:
            if error.reason == EnqueuingDownloadFailureReason.LINK_BROKEN:
                add_to_blocklist(
                    web_link=link,
                    web_title=None,
                    web_sub_title='Torznab',
                    download_link=None,
                    source=None,
                    volume_id=volume_id,
                    issue_id=issue_id,
                    reason=BlocklistReason.LINK_BROKEN
                )

            LOGGER.warning(
                'Unable to add Torznab download; fail_reason="%s"',
                error.reason.value
            )
            raise
