import asyncio
import time
import unittest
from unittest.mock import patch

from backend.features import metadata as md


class _Sleeper:
    """A provider that accepts the question and never answers."""

    async def search_volumes(self, query):
        await asyncio.sleep(600)


class _Quick:
    def __init__(self, title='Crimson After Hours'):
        self.title = title

    async def search_volumes(self, query):
        return [{'title': self.title, 'comicvine_id': 1}]


class _Unavailable:
    @staticmethod
    def is_unavailable_error(error):
        return True


class _Fatal:
    @staticmethod
    def is_unavailable_error(error):
        return False


def _providers(mapping, provider_class=_Unavailable, timeout=0.5):
    """Patch the fan-out to use `mapping`, with a short budget."""
    return (
        patch.object(md, 'get_metadata_provider', lambda pid: mapping[pid]),
        patch.object(
            md, 'configured_metadata_provider_ids', lambda cap: list(mapping)
        ),
        patch.object(
            md.MetadataProviderRegistry, 'provider_class',
            staticmethod(lambda pid: provider_class)
        ),
        patch.object(md, 'METADATA_SEARCH_TIMEOUT', timeout),
    )


class a_provider_that_goes_quiet_does_not_hold_the_search(unittest.TestCase):
    """Silas, 2026-09-03: the Edit Metadata Match dialog sat on "Searching
    metadata providers…" and the whole site became unreachable from that
    browser.

    Nothing bounded the search. `AsyncSession` sets `connect` and
    `sock_read` but no `total`, so a provider answering slowly enough trips
    neither, and five retries with backoff on top can hold one search for
    minutes. `gather` then waits for the slowest.

    A browser opens about six connections per host, so a handful of those
    is enough to make every other page on the site unreachable too -- which
    is why this reads as the browser freezing rather than one slow dialog.
    """

    def _search(self, mapping, **kwargs):
        patches = _providers(mapping, **kwargs)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        started = time.monotonic()
        try:
            result = asyncio.run(
                md.search_metadata_with_fallback('Crimson After Hours 02')
            )
            return result, time.monotonic() - started, None
        except BaseException as error:
            return None, time.monotonic() - started, error

    def test_the_others_answer_without_it(self):
        result, took, error = self._search(
            {'slow': _Sleeper(), 'gcd': _Quick()}
        )

        self.assertIsNone(error)
        self.assertEqual(len(result), 1)
        self.assertLess(took, 5, 'should not have waited for the quiet one')

    def test_every_provider_quiet_still_returns_control(self):
        "An error the dialog can show beats a request that never comes back."
        result, took, error = self._search(
            {'slow': _Sleeper(), 'slower': _Sleeper()}
        )

        self.assertIsNone(result)
        self.assertIsInstance(error, asyncio.TimeoutError)
        self.assertLess(took, 5)

    def test_a_lone_provider_is_bounded_too(self):
        """There is no second opinion to fall back to, which makes a hang
        worse rather than more acceptable.
        """
        result, took, error = self._search({'only': _Sleeper()})

        self.assertIsInstance(error, asyncio.TimeoutError)
        self.assertLess(took, 5)

    def test_a_real_error_is_still_raised_rather_than_swallowed(self):
        "An unavailable provider is a degraded search; a bug is a bug."

        class _Broken:
            async def search_volumes(self, query):
                raise ValueError('something is actually wrong')

        result, _, error = self._search(
            {'broken': _Broken(), 'gcd': _Quick()}, provider_class=_Fatal
        )

        self.assertIsNone(result)
        self.assertIsInstance(error, ValueError)

    def test_nothing_is_bounded_away_when_providers_answer(self):
        result, _, error = self._search(
            {'a': _Quick('One'), 'b': _Quick('Two')}
        )

        self.assertIsNone(error)
        self.assertEqual(
            sorted(r['title'] for r in result), ['One', 'Two']
        )
