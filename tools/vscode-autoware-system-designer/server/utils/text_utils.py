#!/usr/bin/env python3

"""Text utility functions."""

# Entity names, port names and connection references share one word class.
_WORD_CHARS = "._/-"


def get_word_at_position(line: str, character: int) -> str:
    """Get the word at the given character position."""
    # Find word boundaries
    start = character
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] in _WORD_CHARS):
        start -= 1

    end = character
    while end < len(line) and (line[end].isalnum() or line[end] in _WORD_CHARS):
        end += 1

    return line[start:end]
