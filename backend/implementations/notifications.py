# -*- coding: utf-8 -*-

"""
Outbound notifications -- the Kapowarr equivalent of Sonarr/Radarr's
"Connect" settings. A notification service is a Discord webhook or a
generic webhook URL, subscribed to one or more events (currently
download-complete and import-failed; see `NotificationEvent`). When a
subscribed event happens, every enabled, matching service is sent a
POST request describing it.

Sending is deliberately fire-and-forget: `send_notification()` looks up
the matching services synchronously (a cheap local DB read) but hands
each actual HTTP request off to a shared, bounded worker pool
(`_notification_pool`) and never lets a network failure propagate back
into the caller -- post-processing and the download queue must never
stall or fail because a Discord webhook is slow or unreachable. The pool
is sized (`NOTIFICATION_POOL_MAX_WORKERS`) rather than spawning one raw
`Thread` per service per event -- fine at today's scale (a handful of
services, downloads aren't that frequent), but a library with many
enabled services and frequent concurrent downloads could otherwise
accumulate unboundedly many short-lived threads at once (kapowarr/t-016,
kaizen from t-012).

Every request (including the interactive "Test" button) goes through
`_post_bounded()`, which caps the whole call -- across all of `Session`'s
own retries and backoff sleeps, not just a single attempt's socket
timeout -- at NOTIFICATION_REQUEST_TIMEOUT. `Session`'s retry/backoff
(`Constants.TOTAL_RETRIES`/`BACKOFF_FACTOR_RETRIES`) is fine for
occasional requests to a flaky-but-real endpoint, but a `timeout=`
kwarg alone only bounds one attempt's socket wait, not the backoff
sleeps *between* attempts -- against a genuinely unreachable host this
was measured to take 90+ seconds. Same bug, same fix, as
`backend/internals/health.py`'s `HEALTH_CHECK_TIMEOUT`/`_run_bounded`
(kapowarr/t-013).
"""

from concurrent.futures import (Future, ThreadPoolExecutor,
                                TimeoutError as FutureTimeoutError)
from typing import Any, Dict, List, Mapping, Union

from requests import RequestException, Response

from backend.base.custom_exceptions import (InvalidKeyValue, KeyNotFound,
                                            NotificationServiceNotFound)
from backend.base.definitions import NotificationEvent, NotificationServiceType
from backend.base.helpers import Session, normalise_base_url, redact_url
from backend.base.logging import LOGGER
from backend.internals.db import get_db
from backend.internals.server import Server

NOTIFICATION_REQUEST_TIMEOUT = 10.0 # seconds

# Caps how many notification sends (one per matching enabled service per
# event) can be dispatched at once -- see module docstring. Each of these
# workers spends most of its time blocked on _post_bounded's own
# NOTIFICATION_REQUEST_TIMEOUT-bounded wait, not doing CPU work, so a
# modest worker count comfortably covers today's "a handful of services"
# scale while still capping the worst case.
NOTIFICATION_POOL_MAX_WORKERS = 8

# Long-lived and never explicitly shut down -- same lifetime as the app
# process. Idle workers block on the internal queue, which the
# `concurrent.futures` atexit hook signals and joins cleanly on process
# exit, so this doesn't need its own shutdown handling.
_notification_pool = ThreadPoolExecutor(
    max_workers=NOTIFICATION_POOL_MAX_WORKERS,
    thread_name_prefix="NotificationSender"
)


def _raw_post(url: str, payload: Dict[str, Any]) -> Response:
    """Run in the bounding ThreadPoolExecutor's own worker thread (see
    `_post_bounded`), which does not inherit the caller's Flask app
    context. `Session()` needs one (it reads settings via `Settings()`,
    which reads the database via `g`), so push a fresh one here rather
    than relying on whatever thread this happens to run on.
    """
    with Server().app.app_context():
        with Session() as session:
            return session.post(
                url,
                json=payload,
                timeout=NOTIFICATION_REQUEST_TIMEOUT
            )


def _post_bounded(url: str, payload: Dict[str, Any]) -> Union[Response, None]:
    """POST `payload` to `url`, bounded to NOTIFICATION_REQUEST_TIMEOUT no
    matter how many retries/backoff sleeps `Session` attempts internally.

    Deliberately not run on the calling thread with a plain `timeout=`
    kwarg -- that only bounds a single attempt, not the retries around it.
    A thread that's still retrying when the bound is hit is left running
    and discarded (Python has no safe way to cancel a blocking network
    call mid-flight); the executor's `shutdown(wait=False)` lets this
    function return without waiting on it.

    Returns:
        Union[Response, None]: The response, or `None` if the request
            raised or didn't finish within NOTIFICATION_REQUEST_TIMEOUT.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    future: Future = executor.submit(_raw_post, url, payload)
    try:
        return future.result(timeout=NOTIFICATION_REQUEST_TIMEOUT)

    except FutureTimeoutError:
        LOGGER.warning(
            "Notification request to %s didn't finish within %ss",
            url, NOTIFICATION_REQUEST_TIMEOUT
        )
        return None

    except RequestException:
        LOGGER.exception(
            "Failed to send notification to %s", redact_url(url)
        )
        return None

    finally:
        executor.shutdown(wait=False)


def _build_payload(
    service_type: NotificationServiceType,
    event: NotificationEvent,
    title: str,
    message: str
) -> Dict[str, Any]:
    "Build the JSON body to send, tailored to the service type"
    if service_type == NotificationServiceType.DISCORD:
        return {
            "username": "Kapowarr",
            "embeds": [{
                "title": title,
                "description": message
            }]
        }

    # Generic webhook: a plain, source-agnostic body any receiver can parse
    return {
        "event": event.value,
        "title": title,
        "message": message
    }


def _send_one(
    service_type: NotificationServiceType,
    url: str,
    event: NotificationEvent,
    title: str,
    message: str
) -> None:
    "Actually perform the HTTP request. Intended to be run in a thread."
    response = _post_bounded(
        url, _build_payload(service_type, event, title, message)
    )
    if response is not None and response.status_code >= 400:
        LOGGER.warning(
            "Notification request to %s failed with status %d",
            url, response.status_code
        )
    return


def send_notification(
    event: NotificationEvent,
    title: str,
    message: str
) -> None:
    """Notify every enabled service subscribed to this event. Never raises;
    all sending happens on the shared `_notification_pool` so the caller
    is never blocked or interrupted by a notification failure.

    Args:
        event (NotificationEvent): The event that occurred.
        title (str): Short title for the notification.
        message (str): Human-readable body of the notification.
    """
    services = get_db().execute(
        """
        SELECT service_type, url, events
        FROM notification_services
        WHERE enabled = 1;
        """
    ).fetchalldict()

    for service in services:
        if event.value not in service['events'].split(','):
            continue

        try:
            service_type = NotificationServiceType(service['service_type'])
        except ValueError:
            LOGGER.error(
                "Notification service has unknown type, skipping: %s",
                service['service_type']
            )
            continue

        _notification_pool.submit(
            _send_one, service_type, service['url'], event, title, message
        )

    return


class NotificationService:
    required_fields = ('title', 'url')

    @property
    def id(self) -> int:
        return self._id

    def __init__(self, notification_service_id: int) -> None:
        data = get_db().execute(
            """
            SELECT id, service_type, title, url, events, enabled
            FROM notification_services
            WHERE id = ?
            LIMIT 1;
            """,
            (notification_service_id,)
        ).fetchonedict()

        if data is None:
            raise NotificationServiceNotFound(notification_service_id)

        self._id = data['id']
        self._service_type = data['service_type']
        self._title = data['title']
        self._url = data['url']
        self._events = [e for e in data['events'].split(',') if e]
        self._enabled = bool(data['enabled'])
        return

    def get_data(self) -> Dict[str, Any]:
        return {
            'id': self._id,
            'service_type': self._service_type,
            'title': self._title,
            'url': self._url,
            'events': self._events,
            'enabled': self._enabled
        }

    def update(self, data: Mapping[str, Any]) -> None:
        filtered_data = Notifications._format_data(data, self._service_type)

        get_db().execute(
            """
            UPDATE notification_services
            SET
                title = :title,
                url = :url,
                events = :events,
                enabled = :enabled
            WHERE id = :id;
            """,
            {**filtered_data, "id": self._id}
        )
        self._title = filtered_data['title']
        self._url = filtered_data['url']
        self._events = filtered_data['events'].split(',') if filtered_data['events'] else []
        self._enabled = filtered_data['enabled']
        return

    def delete(self) -> None:
        get_db().execute(
            "DELETE FROM notification_services WHERE id = ?;",
            (self._id,)
        )
        return


class Notifications:
    @staticmethod
    def get_service_types() -> List[str]:
        "Get the list of supported notification service type values"
        return [t.value for t in NotificationServiceType]

    @staticmethod
    def get_event_types() -> List[str]:
        "Get the list of supported notification event values"
        return [e.value for e in NotificationEvent]

    @staticmethod
    def _format_data(
        data: Mapping[str, Any],
        service_type: Union[str, None] = None
    ) -> Dict[str, Any]:
        """Validate and normalise the incoming title/url/events/enabled
        fields shared by add() and update().

        Raises:
            KeyNotFound: A required key is missing.
            InvalidKeyValue: A key has an invalid value.
        """
        for key in ('title', 'url'):
            if key not in data or not data[key]:
                raise KeyNotFound(key)

        events = data.get('events') or []
        if not isinstance(events, list):
            raise InvalidKeyValue('events', events)

        valid_events = Notifications.get_event_types()
        for e in events:
            if e not in valid_events:
                raise InvalidKeyValue('events', e)

        if service_type is not None and service_type not in (
            Notifications.get_service_types()
        ):
            raise InvalidKeyValue('service_type', service_type)

        return {
            'title': data['title'],
            'url': normalise_base_url(data['url']),
            'events': ','.join(events),
            'enabled': bool(data.get('enabled', True))
        }

    @staticmethod
    def test(service_type: str, url: str) -> bool:
        """Send a one-off test notification to the given service. Bounded
        to NOTIFICATION_REQUEST_TIMEOUT (see module docstring) so a dead
        URL fails the "Test" button quickly instead of hanging the request
        for the full multi-retry cycle.

        Raises:
            InvalidKeyValue: `service_type` is not a supported type.

        Returns:
            bool: Whether the test request was accepted (2xx/3xx response).
        """
        try:
            service_type_enum = NotificationServiceType(service_type)
        except ValueError:
            raise InvalidKeyValue('service_type', service_type)

        response = _post_bounded(
            normalise_base_url(url),
            _build_payload(
                service_type_enum,
                NotificationEvent.DOWNLOAD_COMPLETED,
                "Kapowarr",
                "This is a test notification from Kapowarr."
            )
        )

        return response is not None and response.status_code < 400

    @staticmethod
    def add(data: Mapping[str, Any]) -> NotificationService:
        """Add a new notification service.

        Raises:
            KeyNotFound: A required key is missing.
            InvalidKeyValue: A key has an invalid value.
        """
        service_type = data.get('service_type')
        if service_type not in Notifications.get_service_types():
            raise InvalidKeyValue('service_type', service_type)

        formatted_data = Notifications._format_data(data, service_type)

        service_id = get_db().execute(
            """
            INSERT INTO notification_services(
                service_type, title, url, events, enabled
            ) VALUES (
                :service_type, :title, :url, :events, :enabled
            );
            """,
            {**formatted_data, "service_type": service_type}
        ).lastrowid

        return NotificationService(service_id)

    @staticmethod
    def get_all() -> List[Dict[str, Any]]:
        "Get every notification service"
        return [
            {**s, 'events': [e for e in s['events'].split(',') if e]}
            for s in get_db().execute(
                """
                SELECT id, service_type, title, url, events, enabled
                FROM notification_services
                ORDER BY title, id;
                """
            ).fetchalldict()
        ]

    @staticmethod
    def get_one(notification_service_id: int) -> NotificationService:
        return NotificationService(notification_service_id)
