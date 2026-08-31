from fnmatch import fnmatch
from typing import Iterable, Sequence


class Namespace(list[str]):
    """Namespace utility container.

    Keeps namespace segments while providing helper methods for common
    namespace/path transformations.
    """

    def __init__(self, segments: Sequence[str] | None = None):
        super().__init__(segments or [])

    @classmethod
    def from_path(cls, path: str | Sequence[str] | "Namespace" | None) -> "Namespace":
        """Build Namespace from path string or segment sequence.

        Examples:
            "/a/b" -> ["a", "b"]
            "a//b/" -> ["a", "b"]
            ["a", "b"] -> ["a", "b"]
            None -> []
        """
        if path is None:
            return cls()

        if isinstance(path, Namespace):
            return cls(path)

        if isinstance(path, str):
            raw_segments = path.split("/")
        else:
            raw_segments = list(path)

        segments = [str(seg).strip() for seg in raw_segments if str(seg).strip()]
        return cls(segments)

    def to_string(self) -> str:
        """Return the namespace string used by instances.

        Returns an empty string for root namespace to preserve existing behavior.
        """
        return "/" + "/".join(self) if self else "/"


def resolve_common_namespace(namespaces: Iterable[Sequence[str]]) -> list[str]:
    """Resolve longest common namespace prefix from the given namespaces."""
    namespace_list = [list(ns) for ns in namespaces]
    if not namespace_list:
        return []

    common_namespace = namespace_list[0].copy()
    for namespace in namespace_list[1:]:
        max_prefix_len = min(len(common_namespace), len(namespace))
        idx = 0
        while idx < max_prefix_len and common_namespace[idx] == namespace[idx]:
            idx += 1
        common_namespace = common_namespace[:idx]
        if not common_namespace:
            break

    return common_namespace


def resolve_common_namespace_from_paths(paths: Iterable[str]) -> list[str]:
    """Resolve common namespace from node-path style strings.

    Path rules:
      - Glob patterns use static prefix before wildcard.
      - Plain paths are treated as node paths, so the last segment (node name)
        is removed.
    """

    namespace_list = [_path_pattern_to_namespace_segments(path) for path in paths]
    if not namespace_list:
        return []

    return resolve_common_namespace(namespace_list)


def _path_pattern_to_namespace_segments(path_pattern: str) -> list[str]:
    normalized_pattern = normalize_node_group_path(path_pattern)
    if normalized_pattern == "/":
        return []

    segments = [segment for segment in normalized_pattern.split("/") if segment]
    if not segments:
        return []

    wildcard_index = next(
        (idx for idx, segment in enumerate(segments) if any(ch in segment for ch in ["*", "?", "["])),
        None,
    )

    if wildcard_index is not None:
        return segments[:wildcard_index]

    return segments[:-1]


def resolve_namespace(path: str | Sequence[str] | Namespace | None) -> Namespace:
    """Resolve any namespace representation to `Namespace`."""
    return Namespace.from_path(path)


def namespace_paths_equal(
    left: str | Sequence[str] | Namespace | None,
    right: str | Sequence[str] | Namespace | None,
) -> bool:
    """Compare namespaces structurally instead of string comparison."""
    return resolve_namespace(left) == resolve_namespace(right)


def namespace_path_is_descendant(
    path: str | Sequence[str] | Namespace | None,
    parent: str | Sequence[str] | Namespace | None,
    *,
    include_self: bool = True,
) -> bool:
    """Return True when `path` is under `parent` namespace.

    Uses structural comparison after resolving both paths.
    """
    resolved_path = resolve_namespace(path)
    resolved_parent = resolve_namespace(parent)

    if len(resolved_parent) > len(resolved_path):
        return False

    if not include_self and len(resolved_parent) == len(resolved_path):
        return False

    return resolved_path[: len(resolved_parent)] == resolved_parent


def is_root_namespace(path: str | Sequence[str] | Namespace | None) -> bool:
    """Return True when namespace resolves to root."""
    return len(resolve_namespace(path)) == 0


def normalize_node_group_path(raw_path: str) -> str:
    path = raw_path.strip()
    if not path.startswith("/"):
        path = f"/{path}"

    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return path


def node_group_pattern_matches(pattern: str, node_path: str) -> bool:
    normalized_pattern = normalize_node_group_path(pattern)
    normalized_node_path = normalize_node_group_path(node_path)

    # glob-style pattern (supports broad wildcard matching)
    if any(ch in normalized_pattern for ch in ["*", "?", "["]):
        return fnmatch(normalized_node_path, normalized_pattern)

    # plain path: treat as prefix to include all nodes under the path
    if normalized_pattern == "/":
        return True
    return normalized_node_path == normalized_pattern or normalized_node_path.startswith(f"{normalized_pattern}/")
