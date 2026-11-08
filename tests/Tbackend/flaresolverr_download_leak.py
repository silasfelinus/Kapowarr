# -*- coding: utf-8 -*-

"""Regressions for a 46 GB FlareSolverr leak observed on Unraid.

GetComics file links were navigated with Chromium, which saved repeated comic
archives under FlareSolverr's unmounted /app/Downloads directory. Challenge
failures also skipped sessions.destroy, leaving Chromium profiles alive.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientSession
from multidict import CIMultiDict
from requests import Request, Response, Session as RequestsSession

from backend.base.definitions import Constants, GCDownloadSource
from backend.base.helpers import AsyncSession, Session
from backend.implementations import getcomics
from backend.implementations.download_clients import DirectDownload
from backend.implementations.flaresolverr import FlareSolverr


def challenge_headers():
    key, value = Constants.CF_CHALLENGE_HEADER
    return {key: value}


def blocked_response():
    response = Response()
    response.status_code = 403
    response.url = "https://files.example/issue.cbz"
    response.headers.update(challenge_headers())
    response.request = Request("GET", response.url).prepare()
    return response


class StreamedDownloadsStayOutOfChromium(unittest.TestCase):
    def test_streamed_binary_request_never_calls_flaresolverr(self):
        with patch("backend.implementations.flaresolverr.FlareSolverr") as fs_class:
            fs = fs_class.return_value
            fs.get_ua_cookies.return_value = ("TestUA", {})
            with patch.object(RequestsSession, "request", return_value=blocked_response()):
                with Session() as session:
                    response = session.get(
                        "https://files.example/issue.cbz", stream=True
                    )

        self.assertEqual(response.status_code, 403)
        fs.handle_cf_block.assert_not_called()

    def test_normal_html_challenges_still_use_flaresolverr(self):
        with patch("backend.implementations.flaresolverr.FlareSolverr") as fs_class:
            fs = fs_class.return_value
            fs.get_ua_cookies.return_value = ("TestUA", {})
            fs.handle_cf_block.return_value = None
            with patch.object(RequestsSession, "request", return_value=blocked_response()):
                with Session() as session:
                    session.get("https://getcomics.org/article")

        fs.handle_cf_block.assert_called_once()


class BrowserSessionCleanup(unittest.TestCase):
    def test_sync_challenge_failure_destroys_its_session(self):
        solver = FlareSolverr.__new__(FlareSolverr)
        solver.base_url = "http://flaresolverr:8191"
        solver.proxy_data = None

        with patch("backend.implementations.flaresolverr.Session"):
            with patch.object(
                FlareSolverr, "_FlareSolverr__api_request",
                side_effect=[
                    {"session": "browser-one"},
                    RuntimeError("challenge failed"),
                    {"status": "ok"},
                ],
            ) as api:
                with self.assertRaisesRegex(RuntimeError, "challenge failed"):
                    solver.handle_cf_block(
                        "https://getcomics.org/article", challenge_headers()
                    )

        self.assertEqual(
            [call.args[2]["cmd"] for call in api.call_args_list],
            ["sessions.create", "request.get", "sessions.destroy"],
        )


class AsyncDownloadAndSessionCleanup(unittest.IsolatedAsyncioTestCase):
    async def test_async_binary_request_cannot_invoke_browser(self):
        response = SimpleNamespace(
            status=403,
            url="https://files.example/issue.cbz",
            headers=challenge_headers(),
            text=AsyncMock(return_value="blocked"),
        )
        with patch("backend.implementations.flaresolverr.FlareSolverr") as fs_class:
            fs = fs_class.return_value
            fs.get_ua_cookies.return_value = ("TestUA", {})
            fs.handle_cf_block_async = AsyncMock(return_value=None)
            with patch.object(
                ClientSession, "_request", new_callable=AsyncMock,
                return_value=response
            ) as underlying:
                async with AsyncSession() as session:
                    result = await session._request(
                        "GET", "https://files.example/issue.cbz",
                        allow_flaresolverr=False
                    )

        self.assertIs(result, response)
        fs.handle_cf_block_async.assert_not_awaited()
        self.assertNotIn("allow_flaresolverr", underlying.await_args.kwargs)

    async def test_direct_getcomics_resolution_opts_out_of_browser(self):
        response = SimpleNamespace(
            ok=True,
            real_url="https://files.example/issue.cbz",
            headers=CIMultiDict({"Content-Type": "application/zip"}),
        )
        fake_session = MagicMock()
        fake_session.get = AsyncMock(return_value=response)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=fake_session)
        context.__aexit__ = AsyncMock(return_value=False)

        link = "https://getcomics.org/download?id=123"
        with patch.object(getcomics, "AsyncSession", return_value=context):
            url, download_type = await getcomics.__purify_link(
                GCDownloadSource.GETCOMICS, link
            )

        self.assertEqual(url, response.real_url)
        self.assertIs(download_type, DirectDownload)
        fake_session.get.assert_awaited_once_with(
            link, allow_flaresolverr=False
        )

    async def test_async_challenge_failure_destroys_its_session(self):
        solver = FlareSolverr.__new__(FlareSolverr)
        solver.base_url = "http://flaresolverr:8191"
        solver.proxy_data = None
        solver.session_semaphore = None

        with patch.object(
            FlareSolverr, "_FlareSolverr__async_api_request",
            new_callable=AsyncMock
        ) as api:
            api.side_effect = [
                {"session": "browser-two"},
                RuntimeError("challenge failed"),
                {"status": "ok"},
            ]
            with self.assertRaisesRegex(RuntimeError, "challenge failed"):
                await solver.handle_cf_block_async(
                    object(), "https://getcomics.org/article",
                    challenge_headers()
                )

        self.assertEqual(
            [call.args[2]["cmd"] for call in api.call_args_list],
            ["sessions.create", "request.get", "sessions.destroy"],
        )
