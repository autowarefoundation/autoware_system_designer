#!/usr/bin/env python3

"""URI utility functions."""

from urllib.parse import urlparse, unquote


def uri_to_path(uri: str) -> str:
    """Convert URI to file path."""
    parsed = urlparse(uri)
    return unquote(parsed.path)


def path_to_uri(path: str) -> str:
    """Convert file path to URI."""
    return f"file://{path}"
