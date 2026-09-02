# -*- coding: utf-8 -*-

from __future__ import annotations

from asyncio import Semaphore
from typing import TYPE_CHECKING, Any, Dict, Mapping, Tuple, Union
from urllib.parse import urlsplit

from requests import RequestException

from backend.base.definitions import Constants, ProxyType
from backend.base.helpers import Session
from backend.base.logging import LOGGER
from backend.internals.settings import Settings

if TYPE_CHECKING:
    from backend.base.helpers import AsyncSession


def challenge_headers(headers: Mapping[str, str]) -> bool:
    """Whether a rejected response is Cloudflare asking for a challenge.

    One header decides it, and it is a fairly new one that Cloudflare does not
    always send. If a 403 arrives from behind Cloudflare without it, this
    returns False and FlareSolverr never gets asked -- which looks exactly
    like FlareSolverr being broken. So the near miss is logged, and a real one
    will say so in the log rather than being silent.

    Args:
        headers (Mapping[str, str]): The response headers.

    Returns:
        bool: Whether to hand the URL to FlareSolverr.
    """
    name, value = Constants.CF_CHALLENGE_HEADER
    if headers.get(name) == value:
        return True

    if 'cloudflare' in str(headers.get('server', '')).lower():
        LOGGER.warning(
            'Refused by Cloudflare without a %s: %s header, so FlareSolverr '
            'was not asked. If this is a challenge, that header is what '
            'Kapowarr looks for.',
            name, value
        )

    return False


def cleared_scope(url: str) -> str:
    """What a FlareSolverr clearance applies to.

    Cloudflare issues `cf_clearance` against a domain, not a page, so that is
    what the solved cookies and user agent have to be filed under. They were
    filed under the exact URL including its query string, which meant a
    clearance won for `getcomics.org/?s=Kaya+36` was never found again for
    `getcomics.org/?s=Hellboy+11`: every request re-challenged, and every
    challenge spun up and tore down a fresh FlareSolverr session to redo work
    that was already done. Solving once per search is indistinguishable from
    FlareSolverr not working -- Silas, on whether it was even wired up: "I
    thought that would help some of our issues, but it seems to do nothing."

    Args:
        url (str): The URL being cleared, or asked about.

    Returns:
        str: The host the clearance belongs to.
    """
    return urlsplit(url).netloc.lower()


class FlareSolverr:
    cookie_mapping: Dict[str, Dict[str, str]] = {}
    "Solved cookies, by host. See `cleared_scope`."

    ua_mapping: Dict[str, str] = {}
    "The user agent each clearance was won with, by host."

    def __init__(self) -> None:
        settings = Settings().sv
        self.session_semaphore: Union[Semaphore, None] = None

        self.base_url = settings.flaresolverr_base_url or None

        self.proxy_data: Union[Dict[str, Any], None] = None
        if settings.proxy_type != ProxyType.NONE:
            self.proxy_data = {
                "proxy": {
                    "url": "%s://%s:%d" % (
                        settings.proxy_type.value.rstrip('h'),
                        settings.proxy_host, settings.proxy_port
                    )
                }
            }
            if settings.proxy_username and settings.proxy_password:
                self.proxy_data["proxy"]["username"] = settings.proxy_username
                self.proxy_data["proxy"]["password"] = settings.proxy_password

        return

    @staticmethod
    def __api_request(
        base_url: str,
        session: Session,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        return session.post(
            base_url + Constants.FS_API_BASE,
            json=data,
            headers={'Content-Type': 'application/json'}
        ).json()

    @staticmethod
    async def __async_api_request(
        base_url: str,
        session: AsyncSession,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        return await (await session.post(
            base_url + Constants.FS_API_BASE,
            json=data,
            headers={'Content-Type': 'application/json'}
        )).json()

    @staticmethod
    def test_flaresolverr(base_url: str) -> bool:
        """Test the connection to a FlareSolverr instance.

        Args:
            base_url (str): The base URL of the FlareSolverr instance. Supply
                base URL without API extension.

        Returns:
            bool: Whether the connection was successful.
        """
        with Session() as session:
            try:
                result = session.get(f"{base_url}/health")

                if result.status_code != 200:
                    return False

                result = result.json()
                if not (
                    result.get("status") == "ok"
                    # Byparr doesn't return the same, even though it states it's
                    # a drop-in replacement
                    or "is working" in result.get("msg", "")
                ):
                    return False

            except RequestException:
                return False
        return True

    def is_enabled(self) -> bool:
        """Check if FlareSolverr is enabled.

        Returns:
            bool: Whether FlareSolverr is enabled.
        """
        return self.base_url is not None

    def get_ua_cookies(self, url: str) -> Tuple[str, Dict[str, str]]:
        """Get the user agent and cookies for a certain URL. The UA and cookies
        can be cleared by CF, so use them to avoid challenges. In case the URL
        is not CF protected, or hasn't explicitly been cleared yet, then the
        default UA is returned and no cookie definitions.

        Args:
            url (str): The URL to get the UA and cookies for.

        Returns:
            Tuple[str, Dict[str, str]]: First element is the UA, or default
                UA. Second element is a mapping of any extra cookies.
        """
        scope = cleared_scope(url)
        return (
            self.ua_mapping.get(scope, Constants.DEFAULT_USERAGENT),
            self.cookie_mapping.get(scope, {})
        )

    def handle_cf_block(
        self,
        url: str,
        headers: Mapping[str, str]
    ) -> Union[None, Dict[str, Any]]:
        """Let FS handle a URL to aquire cleared cookies and UA. These become
        available using `get_ua_cookies()` after this method completes.

        Args:
            url (str): The URL to clear.
            headers (Mapping[str, str]): The response headers from the
                (possibly) blocked request.

        Returns:
            Union[None, Dict[str, Any]]: None if FlareSolverr wasn't needed or
                couldn't solve the problem, or a dictionary with the FlareSolverr
                response.
        """
        if not challenge_headers(headers):
            # Request not failed because of CF block
            return

        if not self.base_url:
            LOGGER.warning(
                "Request blocked by CloudFlare and FlareSolverr not setup"
            )
            return

        with Session() as session:
            # The reason we manually create and close a session for one request
            # is that it's way faster than making just the request and letting
            # FS make the temporary session itself. Why it's so much faster to
            # make a session ourselves compared to FlareSolverr making it for
            # one request, I don't know. It's orders of magnitude faster.

            # Start session
            session_id = self.__api_request(
                self.base_url, session,
                {
                    'cmd': 'sessions.create',
                    **(self.proxy_data or {})
                }
            )["session"]

            # Get result
            result = self.__api_request(
                self.base_url, session,
                {
                    'cmd': 'request.get',
                    'session': session_id,
                    'url': url
                }
            )["solution"]

            # Close session
            self.__api_request(
                self.base_url, session,
                {
                    'cmd': 'sessions.destroy',
                    'session': session_id
                }
            )

            scope = cleared_scope(url)
            self.ua_mapping[scope] = result["userAgent"]
            self.cookie_mapping[scope] = {
                cookie["name"]: cookie["value"]
                for cookie in result["cookies"]
            }

        return result

    async def handle_cf_block_async(
        self,
        session: AsyncSession,
        url: str,
        headers: Mapping[str, str]
    ) -> Union[None, Dict[str, Any]]:
        """Let FS handle a URL to aquire cleared cookies and UA. These become
        available using `get_ua_cookies()` after this method completes.

        Args:
            session (AsyncSession): The session to make the request to FS with.
            url (str): The URL to clear.
            headers (Mapping[str, str]): The response headers from the
                (possibly) blocked request.

        Returns:
            Union[None, Dict[str, Any]]: None if FlareSolverr wasn't needed or
                couldn't solve the problem, or a dictionary with the FlareSolverr
                response.
        """
        if not challenge_headers(headers):
            # Request not failed because of CF block
            return

        if not self.base_url:
            LOGGER.warning(
                "Request blocked by CloudFlare and FlareSolverr not setup"
            )
            return

        # Technically this makes it a max amount of FS sessions per AsyncSession
        # instance. Luckily, for the most intense request scenario of searching
        # for downloads, just one session is used so that works out. We just
        # need to refactor the FlareSolverr implementation to stand more as a
        # separate entity from the Session and AsyncSession classes so that we
        # can regulate session count and session instances better.
        if self.session_semaphore is None:
            self.session_semaphore = Semaphore(
                Constants.MAX_CONCURRENT_FS_SESSIONS
            )

        # Start session
        async with self.session_semaphore:
            session_id = (await self.__async_api_request(
                self.base_url, session,
                {
                    'cmd': 'sessions.create',
                    **(self.proxy_data or {})
                }
            ))["session"]

            # Get result
            result = (await self.__async_api_request(
                self.base_url, session,
                {
                    'cmd': 'request.get',
                    'session': session_id,
                    'url': url
                }
            ))["solution"]

            # Close session
            await self.__async_api_request(
                self.base_url, session,
                {
                    'cmd': 'sessions.destroy',
                    'session': session_id
                }
            )

        scope = cleared_scope(url)
        self.ua_mapping[scope] = result["userAgent"]
        self.cookie_mapping[scope] = {
            cookie["name"]: cookie["value"]
            for cookie in result["cookies"]
        }

        return result
