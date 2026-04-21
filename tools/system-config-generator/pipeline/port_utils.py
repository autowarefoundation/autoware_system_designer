"""Port name utilities shared across emitters and connection resolver."""

from __future__ import annotations


def shorten_port_names(names: list[str]) -> dict[str, str]:
    """Return {original: shortest_unique_suffix} split by '/' segments.

    Each name is shortened to the minimum number of trailing slash-separated
    segments that uniquely identifies it within the list.
    """
    segments_map = {name: name.split("/") for name in names}
    result: dict[str, str] = {}
    for name in names:
        segs = segments_map[name]
        for k in range(1, len(segs) + 1):
            suffix = "/".join(segs[-k:])
            if not any(
                suffix == "/".join(other_segs[-min(k, len(other_segs)) :])
                for other, other_segs in segments_map.items()
                if other != name
            ):
                result[name] = suffix
                break
        else:
            result[name] = name
    return result
