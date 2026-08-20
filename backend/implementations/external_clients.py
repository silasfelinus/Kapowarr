# -*- coding: utf-8 -*-

from concurrent.futures import (ThreadPoolExecutor,
                                TimeoutError as FutureTimeoutError)
from sqlite3 import IntegrityError
from typing import Any, Callable, Dict, List, Mapping, Type, Union

from backend.base.custom_exceptions import (ClientNotWorking,
                                            CredentialInvalid,
                                            ExternalClientDownloading,
                                            ExternalClientNotFound,
                                            InvalidKeyValue, KeyNotFound)
from backend.base.definitions import (BrokenClientReason, ClientTestResult,
                                      DownloadType, ExternalDownloadClient)
from backend.base.helpers import get_subclasses, normalise_base_url
from backend.base.logging import LOGGER
from backend.features.acquisition_preferences import (client_priority,
                                                       remove_client_priority)
from backend.internals.db import get_db
from backend.internals.server import Server

# Deliberately shorter than a Session's full retry cycle (several retries
# with exponential backoff -- see Constants.TOTAL_RETRIES/
# BACKOFF_FACTOR_RETRIES). Matches backend/internals/health.py's
# HEALTH_CHECK_TIMEOUT bound: a user clicking "Test" against an
# unreachable client should see a result in roughly ten seconds, not wait
# out the full retry cycle (observed to take 90+ seconds live against a
# non-responding host in testing).
EXTERNAL_CLIENT_TEST_TIMEOUT = 10.0 # seconds


class _ClientTestTimeout(Exception):
    """Internal marker: a client's `test()` call didn't finish within
    EXTERNAL_CLIENT_TEST_TIMEOUT. Each `_test_client_bounded()` caller
    converts this into whatever it needs to raise/return -- callers that
    need to keep raising (ExternalClients.add(),
    BaseExternalClient.update_client()) convert it to `ClientNotWorking`;
    ExternalClients.test() converts it to a `ClientTestResult` with a more
    specific "timed out" description than a plain connection failure.
    """


def _test_client_bounded(
    test_func: Callable[
        [str, Union[str, None], Union[str, None], Union[str, None]],
        None
    ],
    base_url: str,
    username: Union[str, None],
    password: Union[str, None],
    api_token: Union[str, None]
) -> None:
    """Run a client's `test` classmethod bounded by
    EXTERNAL_CLIENT_TEST_TIMEOUT, letting the original exception on
    failure (ClientNotWorking, CredentialInvalid) propagate to the
    caller -- shared by ExternalClients.test() (the interactive "Test"
    button) and ExternalClients.add()/BaseExternalClient.update_client()
    (the add/edit write paths), which otherwise inherited the full,
    unbounded Session retry/backoff cycle (90+ seconds against an
    unreachable host) the Test button already had bounded.

    Not a `with ThreadPoolExecutor(...) as executor:` block: its
    __exit__ calls shutdown(wait=True), which would block returning a
    result until the thread finishes even after we've already given up
    on it via future.result's timeout.

    Raises:
        ClientNotWorking: Can't connect to client.
        CredentialInvalid: Credentials are invalid.
        _ClientTestTimeout: Didn't finish within EXTERNAL_CLIENT_TEST_TIMEOUT.
    """
    def _run_in_context() -> None:
        # The executor's worker thread doesn't inherit the caller's Flask
        # app context, and a client's test() can need one (e.g. it reads
        # settings via Settings(), which reads the database via `g`) --
        # same gap notifications.py's _raw_post() already hit and fixed
        # for send_notification()'s worker threads.
        with Server().app.app_context():
            test_func(base_url, username, password, api_token)
        return

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_run_in_context)
        future.result(timeout=EXTERNAL_CLIENT_TEST_TIMEOUT)

    except FutureTimeoutError:
        LOGGER.warning(
            "External client test didn't finish within %ss",
            EXTERNAL_CLIENT_TEST_TIMEOUT
        )
        raise _ClientTestTimeout()

    finally:
        executor.shutdown(wait=False)

    return


def _test_client_bounded_raising(
    test_func: Callable[
        [str, Union[str, None], Union[str, None], Union[str, None]],
        None
    ],
    base_url: str,
    username: Union[str, None],
    password: Union[str, None],
    api_token: Union[str, None]
) -> None:
    """`_test_client_bounded()` for callers that need a timeout reported
    the same way as any other connection failure -- ExternalClients.add()
    and BaseExternalClient.update_client(), which both document
    "Raises ClientNotWorking: Can't connect to client" and have no
    separate "timed out" outcome to report.

    Raises:
        ClientNotWorking: Can't connect to client (including a bounded
        timeout, reported as a connection error).
        CredentialInvalid: Credentials are invalid.
    """
    try:
        _test_client_bounded(test_func, base_url, username, password, api_token)
    except _ClientTestTimeout:
        raise ClientNotWorking(BrokenClientReason.CONNECTION_ERROR)
    return


# =====================
# region Base External Client
# =====================
class BaseExternalClient(ExternalDownloadClient):
    required_tokens = ('title', 'base_url')

    @property
    def id(self) -> int:
        return self._id

    @property
    def title(self) -> str:
        return self._title

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def username(self) -> Union[str, None]:
        return self._username

    @property
    def password(self) -> Union[str, None]:
        return self._password

    @property
    def api_token(self) -> Union[str, None]:
        return self._api_token

    def __init__(self, client_id: int) -> None:
        self._id = client_id
        data = get_db().execute("""
            SELECT
                download_type, client_type,
                title, base_url,
                username, password,
                api_token
            FROM external_download_clients
            WHERE id = ?
            LIMIT 1;
            """,
            (client_id,)
        ).fetchone()
        self._title = data['title']
        self._base_url = data['base_url']
        self._username = data['username']
        self._password = data['password']
        self._api_token = data['api_token']
        return

    def get_client_data(self) -> Dict[str, Any]:
        return {
            'id': self._id,
            'download_type': self.download_type.value,
            'client_type': self.client_type,
            'title': self._title,
            'base_url': self._base_url,
            'username': self._username,
            'password': self._password,
            'api_token': self._api_token,
            'priority': client_priority(self._id)
        }

    def update_client(self, data: Mapping[str, Any]) -> None:
        cursor = get_db()
        if cursor.execute(
            "SELECT 1 FROM download_queue WHERE external_client_id = ? LIMIT 1;",
            (self.id,)
        ).fetchone() is not None:
            raise ExternalClientDownloading(self.id)

        filtered_data: Dict[str, Any] = {}
        for key in ('title', 'base_url', 'username', 'password', 'api_token'):
            if key in self.required_tokens and key not in data:
                raise KeyNotFound(key)

            if key in ('title', 'base_url') and data[key] is None:
                raise InvalidKeyValue(key, None)

            if key == 'base_url':
                filtered_data[key] = normalise_base_url(data[key])

            elif key in self.required_tokens:
                filtered_data[key] = data[key]

            else:
                filtered_data[key] = None

        if (
            filtered_data['username'] is not None
            and filtered_data['password'] is None
        ):
            # Username given but not password
            raise InvalidKeyValue('password', filtered_data['password'])

        # Raises exception on fail. Bounded the same way
        # ExternalClients.test() bounds the interactive "Test" button --
        # see _test_client_bounded_raising().
        _test_client_bounded_raising(
            self.test,
            filtered_data['base_url'],
            filtered_data['username'],
            filtered_data['password'],
            filtered_data['api_token']
        )

        cursor.execute("""
            UPDATE external_download_clients
            SET
                title = :title,
                base_url = :base_url,
                username = :username,
                password = :password,
                api_token = :api_token
            WHERE id = :id;
            """,
            {
                **filtered_data,
                "id": self._id
            }
        )
        self._title = filtered_data["title"]
        self._base_url = filtered_data["base_url"]
        self._username = filtered_data["username"]
        self._password = filtered_data["password"]
        self._api_token = filtered_data["api_token"]

        return

    def delete_client(self) -> None:
        try:
            get_db().execute(
                "DELETE FROM external_download_clients WHERE id = ?;",
                (self.id,)
            )

        except IntegrityError:
            raise ExternalClientDownloading(self._id)

        remove_client_priority(self._id)
        return


# =====================
# region Clients
# =====================
class ExternalClients:
    @staticmethod
    def get_client_types() -> Dict[str, Type[ExternalDownloadClient]]:
        """Get a mapping of the client type strings to their class.

        Returns:
            Dict[str, Type[ExternalDownloadClient]]: The mapping.
        """
        from backend.implementations.torrent_clients import (Transmission,
                                                             qBittorrent)
        from backend.implementations.usenet_clients import NZBGet, SABnzbd
        return {
            client.client_type: client
            for client in sorted(
                get_subclasses(BaseExternalClient),
                key=lambda c: c.client_type.lower()
            )
        }

    @staticmethod
    def test(
        client_type: str,
        base_url: str,
        username: Union[str, None],
        password: Union[str, None],
        api_token: Union[str, None]
    ) -> ClientTestResult:
        """Test if an external client is supported, working and available.

        Args:
            client_type (str): The client type, which is the value of the
            client's `client_type` attribute.

            base_url (str): The base URL of the client.

            username (Union[str, None]): The username to use when authenticating
            to the client.
                Allowed to be `None` if not applicable.

            password (Union[str, None]): The password to use when authenticating
            to the client.
                Allowed to be `None` if not applicable.

            api_token (Union[str, None]): The api token to use when authenticating
            to the client.
                Allowed to be `None` if not applicable.

        Raises:
            InvalidKeyValue: One of the parameters has an invalid argument.

        Returns:
            ClientTestResult: Whether the test was successful.
        """
        try:
            client_class = ExternalClients.get_client_types()[client_type]
        except KeyError:
            raise InvalidKeyValue('type', client_type)

        try:
            _test_client_bounded(
                client_class.test,
                normalise_base_url(base_url),
                username,
                password,
                api_token
            )

        except _ClientTestTimeout:
            return ClientTestResult({
                'success': False,
                'description':
                    'Timed out waiting for a response. It may be slow to '
                    'respond or unreachable.'
            })

        except ClientNotWorking as e:
            return ClientTestResult({
                'success': False,
                'description': e.reason_text
            })

        except CredentialInvalid:
            return ClientTestResult({
                'success': False,
                'description': 'Failed to login with the given credentials'
            })

        else:
            return ClientTestResult({
                'success': True,
                'description': None
            })

    @staticmethod
    def add(
        client_type: str,
        title: str,
        base_url: str,
        username: Union[str, None],
        password: Union[str, None],
        api_token: Union[str, None]
    ) -> ExternalDownloadClient:
        """Add an external client.

        Args:
            client_type (str): The client type, which is the value of the
            client's `client_type` attribute.

            title (str): The title to give the client.

            base_url (str): The base URL of the client.

            username (Union[str, None]): The username to use when authenticating
            to the client.
                Allowed to be `None` if not applicable.

            password (Union[str, None]): The password to use when authenticating
            to the client.
                Allowed to be `None` if not applicable.

            api_token (Union[str, None]): The api token to use when authenticating
            to the client.
                Allowed to be `None` if not applicable.

        Raises:
            InvalidKeyValue: One of the parameters has an invalid argument.
            ClientNotWorking: Can't connect to client.
            CredentialInvalid: Credentials are invalid.

        Returns:
            ExternalDownloadClient: The new client.
        """
        if title is None:
            raise InvalidKeyValue('title', title)

        if base_url is None:
            raise InvalidKeyValue('base_url', base_url)

        if username is not None and password is None:
            raise InvalidKeyValue('password', password)

        try:
            ClientClass = ExternalClients.get_client_types()[client_type]
        except KeyError:
            raise InvalidKeyValue('type', client_type)

        # Bounded the same way ExternalClients.test() bounds the
        # interactive "Test" button -- see _test_client_bounded_raising().
        _test_client_bounded_raising(
            ClientClass.test,
            normalise_base_url(base_url),
            username,
            password,
            api_token
        )

        data = {
            'download_type': ClientClass.download_type.value,
            'client_type': client_type,
            'title': title,
            'base_url': normalise_base_url(base_url),
            'username': username,
            'password': password,
            'api_token': api_token
        }
        data = {
            k: (
                v
                if k in (
                    *ClientClass.required_tokens,
                    'download_type', 'client_type'
                ) else
                None
            )
            for k, v in data.items()
        }

        client_id = get_db().execute(
            """
            INSERT INTO external_download_clients(
                download_type, client_type,
                title, base_url,
                username, password, api_token
            ) VALUES (
                :download_type, :client_type,
                :title, :base_url,
                :username, :password, :api_token
            );
            """,
            data
        ).lastrowid
        return ExternalClients.get_client(client_id)

    @staticmethod
    def get_clients() -> List[Dict[str, Any]]:
        """Get a list of all external clients.

        Returns:
            List[Dict[str, Any]]: The list with all external clients.
        """
        result = get_db().execute("""
            SELECT
                id, download_type, client_type,
                title, base_url,
                username, password,
                api_token
            FROM external_download_clients
            ORDER BY title, id;
            """
        ).fetchalldict()
        for client in result:
            client['priority'] = client_priority(client['id'])
        return result

    @staticmethod
    def get_client(client_id: int) -> ExternalDownloadClient:
        """Get an external client based on it's ID.

        Args:
            id (int): The ID of the external client.

        Raises:
            ExternalClientNotFound: The ID does not link to any client.

        Returns:
            ExternalDownloadClient: The client.
        """
        client_type = get_db().execute("""
            SELECT client_type
            FROM external_download_clients
            WHERE id = ?
            LIMIT 1;
            """,
            (client_id,)
        ).exists()

        if not client_type:
            raise ExternalClientNotFound(client_id)

        return ExternalClients.get_client_types()[client_type](client_id)

    @staticmethod
    def get_least_used_client(
        download_type: DownloadType
    ) -> ExternalDownloadClient:
        """Get the preferred least-used client of a specific download type.

        Client priority is considered first (1 is highest). Existing load
        balancing is preserved inside the highest-priority tier so two equal
        clients still share work instead of one becoming a permanent hot spot.

        Args:
            download_type (DownloadType): The download type to get the client
            for.

        Raises:
            ExternalClientNotFound: No client of the specified type was found.

        Returns:
            ExternalDownloadClient: The preferred least-used client.
        """
        rows = get_db().execute("""
            SELECT clients.id, COUNT(queue.id) AS queue_count
            FROM external_download_clients clients
            LEFT JOIN download_queue queue
                ON queue.external_client_id = clients.id
            WHERE clients.download_type = ?
            GROUP BY clients.id
            ORDER BY clients.id;
            """,
            (download_type.value,)
        ).fetchall()

        if not rows:
            raise ExternalClientNotFound(-1)

        priorities = {
            row['id']: client_priority(row['id'])
            for row in rows
        }
        best_priority = min(priorities.values())
        eligible = [
            row for row in rows
            if priorities[row['id']] == best_priority
        ]
        selected = min(
            eligible,
            key=lambda row: (row['queue_count'], row['id'])
        )
        return ExternalClients.get_client(selected['id'])
