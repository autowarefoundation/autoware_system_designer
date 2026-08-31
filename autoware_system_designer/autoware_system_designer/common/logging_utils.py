import logging
import sys
from typing import Optional, Set, Tuple


class _MaxLevelFilter(logging.Filter):
    def __init__(self, max_level: int) -> None:
        super().__init__()
        self._max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self._max_level


class _DedupeFilter(logging.Filter):
    """Passes the first occurrence of each (logger, level, rendered message) triple.

    A deployment is resolved once per mode, so a diagnostic about shared design data is
    re-emitted verbatim for every mode. The repeats carry no mode context and therefore
    no information.
    """

    def __init__(self) -> None:
        super().__init__()
        self._seen: Set[Tuple[str, int, str]] = set()

    def filter(self, record: logging.LogRecord) -> bool:
        key = (record.name, record.levelno, record.getMessage())
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


def configure_split_stream_logging(
    *,
    level: int = logging.INFO,
    stderr_level: int = logging.WARNING,
    formatter: Optional[logging.Formatter] = None,
    dedupe_stderr: bool = True,
) -> None:
    """Configure root logging:

    - DEBUG/INFO go to stdout
    - WARNING/ERROR/CRITICAL go to stderr, each distinct message reported once

    This is intended to keep terminals clean while still allowing warnings/errors
    to be visible when callers suppress stdout (e.g., during builds).
    """

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    if formatter is None:
        formatter = logging.Formatter("%(name)s - %(levelname)s - %(message)s")

    if stderr_level < logging.DEBUG:
        stderr_level = logging.DEBUG

    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setLevel(logging.DEBUG)
    stdout_handler.addFilter(_MaxLevelFilter(stderr_level - 1))
    stdout_handler.setFormatter(formatter)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setLevel(stderr_level)
    if dedupe_stderr:
        stderr_handler.addFilter(_DedupeFilter())
    stderr_handler.setFormatter(formatter)

    root.addHandler(stdout_handler)
    root.addHandler(stderr_handler)
