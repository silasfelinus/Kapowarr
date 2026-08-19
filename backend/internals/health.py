# -*- coding: utf-8 -*-

"""
System health checks, surfaced on the Status page as warnings -- the
Kapowarr equivalent of Sonarr/Radarr's "Health" section. Read-only: runs a
handful of cheap connectivity/validity checks on demand and returns a flat
list of warnings, instead of leaving problems only discoverable via the
logs or a silently-stalled queue.

Each individual check catches its own unexpected exceptions and turns them
into a warning rather than letting them propagate -- a broken check (e.g.
no internet connectivity) is itself something worth surfacing here, not a
reason to fail the whole health request.

The two network-bound checks (ComicVine, external clients) are run with a
bounded timeout, independent of whatever retry/backoff behavior the
underlying `Session`/client library applies to a single request. A
`Session` already retries a failing connection several times with
exponential backoff (`Constants.TOTAL_RETRIES`/`BACKOFF_FACTOR_RETRIES`),
which is fine for a one-off, user-initiated "Test" click, but this module
is called on every Status page load -- one unreachable client should
report "timed out" quickly rather than making every visit to the page
wait out the full retry cycle (observed to take over a minute against a
non-responding host in testing).
"""

from concurrent.futures import (ThreadPoolExecutor,
                                TimeoutError as FutureTimeoutError)
from typing import Any, Dict, List

from backend.base.logging import LOGGER
from backend.features.metadata import get_metadata_provider
from backend.implementations.external_clients import ExternalClients
from backend.implementations.root_folders import RootFolders
from backend.internals.settings import Settings

# Deliberately shorter than a Session's full retry cycle -- see module
# docstring. A check that's still running after this is reported as timed
# out; the underlying thread is left to finish and is discarded, since
# Python has no safe way to cancel a blocking network call mid-flight.
HEALTH_CHECK_TIMEOUT = 10.0 # seconds


def get_health_data() -> List[Dict[str, str]]:
    """Run all health checks and gather the warnings found.

    Returns:
        List[Dict[str, str]]: One entry per warning, each with a `source`
            (what the warning is about) and a `message` (human-readable
            description of what's wrong).
    """
    warnings: List[Dict[str, str]] = []

    # Cheap and local -- no need for the bounded-timeout treatment below.
    warnings.extend(_check_root_folders())

    clients = ExternalClients.get_clients()

    # One thread per network-bound check (ComicVine + each external
    # client) so a single unreachable client can't delay the result for
    # an otherwise-healthy one -- each is bounded independently.
    #
    # Deliberately not a `with ThreadPoolExecutor(...) as executor:` block:
    # its __exit__ calls shutdown(wait=True), which would block returning
    # a result until every thread finishes -- including ones this
    # function has already given up on via _run_bounded's timeout. A
    # thread that's still retrying a dead connection when its timeout
    # fires is left running and discarded (Python has no safe way to
    # cancel a blocking network call mid-flight); shutdown(wait=False)
    # lets the process exit without waiting on it.
    executor = ThreadPoolExecutor(max_workers=max(2, len(clients) + 1))
    try:
        comicvine_future = executor.submit(_check_comicvine)
        client_futures = [
            (client, executor.submit(_test_external_client, client))
            for client in clients
        ]

        warnings.extend(_run_bounded(comicvine_future, 'ComicVine'))

        for client, future in client_futures:
            source = f"Download client: {client['title']}"
            warnings.extend(_run_bounded(future, source))

    finally:
        executor.shutdown(wait=False)

    return warnings


def _run_bounded(future, source: str) -> List[Dict[str, str]]:
    """Wait for a health-check future, capped at HEALTH_CHECK_TIMEOUT.

    Args:
        future: The future to wait on.

        source (str): The `source` to report the warning under if the
            future doesn't finish in time.

    Returns:
        List[Dict[str, str]]: Whatever the check found, or a single
            "timed out" warning if it didn't finish in time.
    """
    try:
        return future.result(timeout=HEALTH_CHECK_TIMEOUT)

    except FutureTimeoutError:
        LOGGER.warning(
            "Health check for %s didn't finish within %ss",
            source, HEALTH_CHECK_TIMEOUT
        )
        return [{
            'source': source,
            'message':
                'Timed out waiting for a response. It may be slow to '
                'respond or unreachable.'
        }]


def _check_comicvine() -> List[Dict[str, str]]:
    api_key = Settings().get_settings().comicvine_api_key
    if not api_key:
        return [{
            'source': 'ComicVine',
            'message':
                'No ComicVine API key is set. Searching and metadata '
                'fetching will not work.'
        }]

    try:
        key_works = get_metadata_provider().test_key()

    except Exception:
        LOGGER.exception('Health check: unable to reach ComicVine')
        return [{
            'source': 'ComicVine',
            'message': 'Unable to reach ComicVine to validate the API key.'
        }]

    if not key_works:
        return [{
            'source': 'ComicVine',
            'message':
                'The configured ComicVine API key is invalid, or the '
                'ComicVine rate limit has been reached.'
        }]

    return []


def _test_external_client(client: Dict[str, Any]) -> List[Dict[str, str]]:
    source = f"Download client: {client['title']}"

    try:
        result = ExternalClients.test(
            client['client_type'],
            client['base_url'],
            client['username'],
            client['password'],
            client['api_token']
        )

    except Exception:
        LOGGER.exception(
            'Health check: unable to test external client %s',
            client['title']
        )
        return [{
            'source': source,
            'message': 'Unable to test this client.'
        }]

    if not result['success']:
        return [{
            'source': source,
            'message': result['description'] or 'Unreachable or not responding.'
        }]

    return []


def _check_root_folders() -> List[Dict[str, str]]:
    return [
        {
            'source': 'Root folder',
            'message': f'{root_folder.folder} is missing or inaccessible.'
        }
        for root_folder in RootFolders().get_all()
        if root_folder.size is None
    ]
