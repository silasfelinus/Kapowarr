# -*- coding: utf-8 -*-

"""
Setting up, using and altering the logger
"""

import logging
import logging.config
from io import StringIO
from logging.handlers import RotatingFileHandler
from os import remove
from os.path import exists, isdir, isfile, join
from typing import Any, Union

from backend.base.definitions import Constants


class UpToInfoFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= logging.INFO


# Routine web-server chatter that arrives at WARNING/ERROR for conditions that
# are normal in a self-hosted Kapowarr -- and that borrows Kapowarr's own
# vocabulary, so it reads as a fault in Kapowarr's log.
#
# - waitress logs "Task queue depth is N" whenever more HTTP requests arrive at
#   once than it has idle threads. "Task queue" is a Kapowarr surface (System >
#   Tasks), and this is neither that queue nor a problem: one tablet opening a
#   page full of covers outruns ten threads for a moment.
# - engineio logs "Invalid session <sid>" at ERROR when a browser reconnects a
#   websocket whose session predates a restart, which is what every restart
#   with a tab left open looks like.
#
# Demoted rather than dropped: under real sustained load the queue depth is
# worth seeing, it just is not an error, and hiding it outright would remove
# the evidence for a slow-server report.
DEMOTED_LOGGER_PREFIXES = (
    'waitress.queue',
    'engineio.server',
    'socketio.server',
)


class ThirdPartyNoiseFilter(logging.Filter):
    """Log library chatter at INFO so it stops reading as a Kapowarr fault."""

    def filter(self, record: logging.LogRecord) -> bool:
        if (
            record.levelno > logging.INFO
            and record.name.startswith(DEMOTED_LOGGER_PREFIXES)
        ):
            record.levelno = logging.INFO
            record.levelname = 'INFO'
        return True


class ErrorColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> Any:
        result = super().format(record)
        return f"\033[1;31:40m{result}\033[0m"


class MPRotatingFileHandler(RotatingFileHandler):
    def __init__(self,
        filename,
        mode="a",
        maxBytes=0,
        backupCount=0,
        encoding=None,
        delay=False,
        do_rollover=True
    ) -> None:
        self.do_rollover = do_rollover
        return super().__init__(
            filename, mode, maxBytes, backupCount, encoding, delay
        )

    def shouldRollover(self, record: logging.LogRecord) -> int:
        if not self.do_rollover:
            return 0
        return super().shouldRollover(record)


LOGGER = logging.getLogger(Constants.LOGGER_NAME)
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "[%(asctime)s][%(levelname)s] %(message)s",
            "datefmt": "%H:%M:%S"
        },
        "simple_red": {
            "()": ErrorColorFormatter,
            "format": "[%(asctime)s][%(levelname)s] %(message)s",
            "datefmt": "%H:%M:%S"
        },
        "detailed": {
            "format": "%(asctime)s | %(processName)s | %(threadName)s | %(filename)sL%(lineno)s | %(levelname)s | %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S%z",
        }
    },
    "filters": {
        "up_to_info": {
            "()": UpToInfoFilter
        },
        "third_party_noise": {
            "()": ThirdPartyNoiseFilter
        }
    },
    "handlers": {
        "console_error": {
            "class": "logging.StreamHandler",
            "level": "WARNING",
            "formatter": "simple_red",
            "filters": ["third_party_noise"],
            "stream": "ext://sys.stderr"
        },
        "console": {
            "class": "logging.StreamHandler",
            "level": "DEBUG",
            "formatter": "simple",
            "filters": ["third_party_noise", "up_to_info"],
            "stream": "ext://sys.stdout"
        },
        "file": {
            "()": MPRotatingFileHandler,
            "level": "DEBUG",
            "formatter": "detailed",
            "filters": ["third_party_noise"],
            "filename": "",
            "maxBytes": 1_000_000,
            "backupCount": 1,
            "do_rollover": True
        }
    },
    "loggers": {
        Constants.LOGGER_NAME: {}
    },
    "root": {
        "level": "INFO",
        "handlers": [
            "console",
            "console_error",
            "file"
        ]
    }
}


def setup_logging(
    log_folder: Union[str, None],
    log_file: Union[str, None],
    log_level: Union[int, None] = None,
    do_rollover: bool = True
) -> None:
    """Setup the basic config of the logging module.

    Args:
        log_folder (Union[str, None]): The folder to put the log file in.
            If `None`, the log file will be in the same folder as the
            application folder. It will be created if it doesn't exist yet.

        log_file (Union[str, None]): The filename of the log file.
            If `None`, the default filename will be used.  It will be created if
            it doesn't exist yet.

        log_level (Union[int, None], optional): The log level to set the logger
            to. If `None`, the default level will be used.
            Defaults to None.

        do_rollover (bool, optional): Whether to allow the log file to rollover
            when it reaches the maximum size.
            Defaults to True.

    Raises:
        ValueError: The given log folder is not a folder, or the given log file
            is not a file.
    """
    from backend.base.files import create_folder, folder_path

    if log_folder:
        if exists(log_folder) and not isdir(log_folder):
            raise ValueError("Logging folder is not a folder")

        create_folder(log_folder)

    if log_file:
        if exists(log_file) and not isfile(log_file):
            raise ValueError("Logging file is not a file")
    else:
        log_file = Constants.LOGGER_FILENAME

    if log_folder is None:
        LOGGING_CONFIG["handlers"]["file"]["filename"] = folder_path(log_file)
    else:
        LOGGING_CONFIG["handlers"]["file"]["filename"] = join(
            log_folder,
            log_file
        )

    LOGGING_CONFIG["handlers"]["file"]["do_rollover"] = do_rollover

    logging.config.dictConfig(LOGGING_CONFIG)

    # Log uncaught exceptions using the logger instead of printing to stderr.
    # Logger goes to stderr anyway, so still visible in console but also logs
    # to file, so that downloaded log file also contains any exceptions.
    import sys
    import threading
    from traceback import format_exception

    def log_uncaught_exceptions(e_type, value, tb):
        LOGGER.error(
            "UNCAUGHT EXCEPTION:\n" +
            ''.join(format_exception(e_type, value, tb))
        )
        return

    def log_uncaught_threading_exceptions(args):
        LOGGER.exception(
            f"UNCAUGHT EXCEPTION IN THREAD: {args.exc_value}"
        )
        return

    sys.excepthook = log_uncaught_exceptions
    threading.excepthook = log_uncaught_threading_exceptions

    if log_level is not None:
        set_log_level(log_level)

    return


def get_log_filepath() -> str:
    """Get the filepath to the log file.

    Returns:
        str: The filepath.
    """
    return LOGGING_CONFIG["handlers"]["file"]["filename"]


def clear_log_files() -> None:
    """Empty the log file and its rotation, keeping the handler writing.

    The rotated `.1` file is removed outright, but the live file is truncated
    rather than deleted: the handler holds an open descriptor to it, and on
    POSIX unlinking the path leaves that descriptor writing to a file nothing
    can read any more, so logging would appear to stop until a restart.
    """
    file = get_log_filepath()

    rotated = file + '.1'
    if exists(rotated):
        try:
            remove(rotated)
        except OSError:
            LOGGER.exception('Could not remove rotated log file: %s', rotated)

    for handler in LOGGER.handlers:
        stream = getattr(handler, 'stream', None)
        if stream is not None and getattr(stream, 'name', None) == file:
            handler.acquire()
            try:
                stream.seek(0)
                stream.truncate()
                stream.flush()
            finally:
                handler.release()
            return

    # No handler owns it in this process (a worker, or logging to console
    # only), so there is no descriptor to keep consistent.
    if exists(file):
        with open(file, 'w'):
            pass
    return


def get_log_file_contents() -> StringIO:
    """Get all the logs from the log file(s).

    Raises:
        FileNotFound: The log file does not exist.

    Returns:
        StringIO: The contents of the log file(s).
    """
    from backend.base.custom_exceptions import FileNotFound

    file = get_log_filepath()
    if not exists(file):
        raise FileNotFound(file)

    sio = StringIO()
    for ext in ('.1', ''):
        lf = file + ext
        if not exists(lf):
            continue
        with open(lf, 'r') as f:
            sio.writelines(f)

    return sio


def set_log_level(
    level: Union[int, str]
) -> None:
    """Change the logging level.

    Args:
        level (Union[int, str]): The level to set the logging to.
            Should be a logging level, like `logging.INFO` or `"DEBUG"`.
    """
    if isinstance(level, str):
        level = logging._nameToLevel[level.upper()]

    root_logger = logging.getLogger()
    if root_logger.level == level:
        return

    LOGGER.debug(f'Setting logging level: {level}')
    root_logger.setLevel(level)

    return
