import logging
import sys
from typing import Optional


class _MaxLevelFilter(logging.Filter):
    def __init__(self, max_level: int) -> None:
        super().__init__()
        self._max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self._max_level


def configure_split_stream_logging(
    *,
    level: int = logging.INFO,
    stderr_level: int = logging.WARNING,
    formatter: Optional[logging.Formatter] = None,
) -> None:
    """Configure root logging:

    - DEBUG/INFO go to stdout
    - WARNING/ERROR/CRITICAL go to stderr

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
    stderr_handler.setFormatter(formatter)

    root.addHandler(stdout_handler)
    root.addHandler(stderr_handler)
