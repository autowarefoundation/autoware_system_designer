#!/usr/bin/env python3
"""
Inline autoware_launch config dependencies into parameter_set YAML files.

For each node entry in a *.parameter_set.yaml that has param_files referencing
$(find-pkg-share autoware_launch)/..., this script:

  1. Finds the node.yaml definition that declares the param_file key.
  2. Resolves the default config file from the node's package source.
  3. Compares the autoware_launch override with the default config.
  4. Adds differences as inline param_values.
  5. Removes the autoware_launch param_file reference.

Disambiguation for generic param_file key names (e.g. "param_path"):
  - Prefer the node.yaml whose default filename matches the autoware_launch config filename.
  - If still ambiguous, prefer the node.yaml whose package contains a config file
    matching the autoware_launch config filename.
  - Fall back to the first match.

For the default config, if the node.yaml's declared default is missing but the package
contains a file matching the autoware_launch config filename, that file is used instead.

Usage:
  python3 inline_autoware_launch_params.py <parameter_set.yaml> [OPTIONS]

Options:
  --workspace-src DIR   Root of the colcon src/ tree
                        (default: autodetected relative to this script)
  --dry-run             Print what would change without writing the file
  --output FILE         Write output to FILE instead of modifying in place
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

import ruamel.yaml as _ruamel

_yaml = _ruamel.YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096  # prevent line wrapping
_yaml.indent(mapping=2, sequence=4, offset=2)  # match the original file style

# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------


def _load(path: Path) -> Any:
    with path.open() as f:
        return _yaml.load(f)


def _dump_to_stream(data: Any, stream: Any) -> None:
    _yaml.dump(data, stream)


def _extract_ros_params(data: Any) -> dict:
    """Strip the /**:  ros__parameters:  wrapper and return the parameter dict."""
    if not isinstance(data, dict):
        return {}
    for _ns, body in data.items():
        if isinstance(body, dict) and "ros__parameters" in body:
            return dict(body["ros__parameters"])
    return {}


def _normalise(v: Any) -> Any:
    """Recursively convert ruamel comment-aware types to plain Python equivalents."""
    if isinstance(v, _ruamel.comments.CommentedMap):
        return {k: _normalise(vv) for k, vv in v.items()}
    if isinstance(v, (_ruamel.comments.CommentedSeq, list)):
        return [_normalise(i) for i in v]
    return v


def _prepare_value(v: Any) -> Any:
    """Prepare a normalised value for YAML output as a param_value.

    Lists are wrapped in a flow-style CommentedSeq so they render as [a, b, c]
    instead of a block sequence.  Dicts and scalars are passed through unchanged.
    """
    if isinstance(v, list):
        seq = _ruamel.comments.CommentedSeq([_prepare_value(i) for i in v])
        seq.fa.set_flow_style()
        return seq
    if isinstance(v, dict):
        cm = _ruamel.comments.CommentedMap()
        for k, vv in v.items():
            cm[k] = _prepare_value(vv)
        return cm
    return v


# ---------------------------------------------------------------------------
# Workspace / package discovery
# ---------------------------------------------------------------------------


def _find_workspace_src(start: Path) -> Path:
    """Walk up from *start* until we find a src/ tree inside a colcon workspace."""
    for parent in [start] + list(start.parents):
        if parent.name == "src":
            return parent
    # Fallback: look for src/ as a child containing package.xml files
    candidate = start
    for _ in range(12):
        src = candidate / "src"
        if src.is_dir():
            return src
        candidate = candidate.parent
    raise RuntimeError("Could not locate workspace src/ directory. Use --workspace-src.")


def _find_package_dir(pkg_name: str, workspace_src: Path) -> Optional[Path]:
    """Return the directory that contains package.xml for *pkg_name*."""
    for pkg_xml in workspace_src.rglob("package.xml"):
        if pkg_xml.parent.name == pkg_name:
            return pkg_xml.parent
    return None


# ---------------------------------------------------------------------------
# node.yaml discovery with disambiguation
# ---------------------------------------------------------------------------


def _all_node_yamls_for_param_key(param_key: str, workspace_src: Path) -> list[tuple[Path, dict]]:
    """
    Return all (node_yaml_path, param_file_entry) pairs where the node declares *param_key*.
    """
    results: list[tuple[Path, dict]] = []
    for node_yaml in workspace_src.rglob("*.node.yaml"):
        try:
            data = _load(node_yaml)
        except Exception:
            continue
        for pf in data.get("param_files") or []:
            if isinstance(pf, dict) and pf.get("name") == param_key:
                results.append((node_yaml, dict(pf)))
                break  # each node.yaml has at most one entry per key
    return results


def _path_score(candidate_pkg_name: str, al_config: Path, al_pkg_dir: Path) -> int:
    """
    Score how well *candidate_pkg_name* matches the directory context of *al_config*.

    Extract directory segments from the al_config path relative to the autoware_launch
    package root, then count how many of those segments appear as substrings of the
    candidate package name (longer matches score higher).

    Example: config path  .../detection/object_merger/data_association_matrix.param.yaml
             → dir parts  ["detection", "object_merger"]
             → "object_merger" in "autoware_object_merger" → score = len("object_merger")
    """
    try:
        rel = al_config.relative_to(al_pkg_dir)
    except ValueError:
        rel = al_config
    dir_parts = list(rel.parent.parts)  # directory components only, no filename
    pkg_norm = candidate_pkg_name.replace("-", "_")
    score = 0
    for part in dir_parts:
        part_norm = part.replace("-", "_")
        if part_norm and part_norm in pkg_norm:
            score += len(part_norm)
    return score


def _pick_best_node_yaml(
    candidates: list[tuple[Path, dict]],
    al_config: Path,
    workspace_src: Path,
) -> tuple[Optional[Path], Optional[dict]]:
    """
    Choose the best (node_yaml, pf_entry) for the given autoware_launch config.

    Priority (highest wins):
      1. node.yaml whose declared default filename == al_config.name
      2. node.yaml whose package contains config/<al_config.name>
      3. Highest path-segment score (directory parts of al_config vs. package name)
      4. First candidate
    """
    al_name = al_config.name
    al_pkg_dir = _find_package_dir("autoware_launch", workspace_src) or al_config.parent

    # Priority 1 – exact filename match in declared default
    exact = [(ny, pf) for ny, pf in candidates if Path(pf.get("default", "")).name == al_name]
    if len(exact) == 1:
        return exact[0]

    # Priority 2 – package contains config/<al_name>
    pkg_has_file = []
    for node_yaml, pf in exact or candidates:
        try:
            nd = _load(node_yaml)
        except Exception:
            continue
        pkg_name = (nd.get("package") or {}).get("name", "")
        pkg_dir = _find_package_dir(pkg_name, workspace_src) if pkg_name else None
        if pkg_dir and (pkg_dir / "config" / al_name).exists():
            pkg_has_file.append((node_yaml, pf))
    if len(pkg_has_file) == 1:
        return pkg_has_file[0]

    # Priority 3 – path-segment score (use *exact* subset if non-empty, else all candidates)
    pool = exact or pkg_has_file or candidates
    scored = []
    for node_yaml, pf in pool:
        try:
            nd = _load(node_yaml)
        except Exception:
            nd = {}
        pkg_name = (nd.get("package") or {}).get("name", "")
        s = _path_score(pkg_name, al_config, al_pkg_dir)
        scored.append((s, node_yaml, pf))
    if scored:
        scored.sort(key=lambda x: -x[0])
        best_score = scored[0][0]
        top = [(ny, pf) for s, ny, pf in scored if s == best_score]
        if top:
            return top[0]

    if candidates:
        return candidates[0]
    return None, None


# ---------------------------------------------------------------------------
# Config comparison
# ---------------------------------------------------------------------------


def _diff_params(default: dict, override: dict, _prefix: str = "") -> dict:
    """
    Recursively diff *override* against *default*.

    Returns a flat mapping {dotted.key.path: value} for every leaf (or sub-tree)
    that is absent in *default* or has a different value.

    - Both dicts at the same key → recurse deeper (only the differing sub-keys bubble up).
    - Scalar, list, or structurally different type → record at this depth.
    - Key absent from *default* → record the whole override value at this depth.

    This prevents an identical sibling field (e.g. max_dist) from being written
    to param_values just because a cousin field (e.g. can_assign.unknown) changed.
    """
    diffs: dict = {}
    for key, ov in override.items():
        full_key = f"{_prefix}.{key}" if _prefix else key
        ov_norm = _normalise(ov)
        if key not in default:
            diffs[full_key] = ov
        else:
            df_norm = _normalise(default[key])
            if df_norm == ov_norm:
                pass  # identical — skip
            elif isinstance(ov_norm, dict) and isinstance(df_norm, dict):
                diffs.update(_diff_params(default[key], ov, full_key))
            else:
                diffs[full_key] = ov
    return diffs


# ---------------------------------------------------------------------------
# Core processing logic
# ---------------------------------------------------------------------------


def _resolve_al_path(ref: str, workspace_src: Path) -> Optional[Path]:
    """
    Map  $(find-pkg-share autoware_launch)/config/...
    →    <workspace_src>/launcher/autoware_launch/autoware_launch/config/...
    """
    prefix = "$(find-pkg-share autoware_launch)/"
    if not ref.startswith(prefix):
        return None
    rel = ref[len(prefix) :]
    pkg_dir = _find_package_dir("autoware_launch", workspace_src)
    if pkg_dir is None:
        return None
    p = pkg_dir / rel
    return p if p.exists() else None


def _resolve_default_config(
    node_yaml_path: Path,
    pf_entry: dict,
    al_config: Path,
    workspace_src: Path,
) -> Optional[Path]:
    """
    Return the path to the default (package-local) config for this param_file entry.

    Tries (in order):
      1. pkg_dir / 'config' / al_config.name   (exact filename match — most specific)
      2. pkg_dir / pf_entry['default']          (fallback: what the node.yaml declares)
    """
    try:
        nd = _load(node_yaml_path)
    except Exception:
        return None

    pkg_name = (nd.get("package") or {}).get("name", "")
    default_rel: str = pf_entry.get("default", "")
    if not pkg_name:
        return None

    pkg_dir = _find_package_dir(pkg_name, workspace_src)
    if pkg_dir is None:
        return None

    # Prefer: a file whose name matches the autoware_launch config (most specific match)
    alt = pkg_dir / "config" / al_config.name
    if alt.exists():
        return alt

    # Fallback: what the node.yaml declares
    if default_rel:
        p = pkg_dir / default_rel
        if p.exists():
            return p

    return None


def process(
    parameter_set_path: Path,
    workspace_src: Path,
    dry_run: bool = False,
    output_path: Optional[Path] = None,
) -> bool:
    """
    Process a parameter_set YAML.  Returns True if any change was made.
    """
    data = _load(parameter_set_path)
    parameters = data.get("parameters") or []
    changed = False

    for node_entry in parameters:
        node_path = node_entry.get("node", "<unknown>")
        param_files: list = node_entry.get("param_files") or []
        param_values: list = node_entry.get("param_values") or []

        new_param_files = _ruamel.comments.CommentedSeq()
        node_changed = False

        for pf in param_files:
            if not isinstance(pf, dict):
                new_param_files.append(pf)
                continue

            # Identify a key referencing autoware_launch
            al_key: Optional[str] = None
            al_ref: Optional[str] = None
            for k, v in pf.items():
                if isinstance(v, str) and "$(find-pkg-share autoware_launch)" in v:
                    al_key, al_ref = k, v
                    break

            if al_key is None:
                new_param_files.append(pf)
                continue

            print(f"\nNode : {node_path}")
            print(f"  Key: {al_key}")
            print(f"  Ref: {al_ref}")

            al_config = _resolve_al_path(al_ref, workspace_src)
            if al_config is None:
                print("  WARNING: autoware_launch config not found – skipping")
                new_param_files.append(pf)
                continue

            # --- find best matching node.yaml ---
            candidates = _all_node_yamls_for_param_key(al_key, workspace_src)
            node_yaml_path, pf_entry = _pick_best_node_yaml(candidates, al_config, workspace_src)
            if node_yaml_path is None:
                print(f"  WARNING: no node.yaml declares '{al_key}' – skipping")
                new_param_files.append(pf)
                continue

            # --- resolve the default config file ---
            default_config = _resolve_default_config(node_yaml_path, pf_entry, al_config, workspace_src)
            if default_config is None:
                print("  WARNING: default config not resolved – skipping")
                new_param_files.append(pf)
                continue

            print(f"  node.yaml      : {node_yaml_path.relative_to(workspace_src)}")
            print(f"  default config : {default_config.relative_to(workspace_src)}")
            print(f"  override config: {al_config.relative_to(workspace_src)}")

            # --- compare params ---
            default_params = _extract_ros_params(_load(default_config))
            al_params = _extract_ros_params(_load(al_config))
            diffs = _diff_params(default_params, al_params)

            if diffs:
                print(f"  Differences    : {list(diffs.keys())}")
                existing_names = {pv.get("name") for pv in param_values if isinstance(pv, dict)}
                for k, v in diffs.items():
                    if k not in existing_names:
                        entry = _ruamel.comments.CommentedMap([("name", k), ("value", _prepare_value(_normalise(v)))])
                        param_values.append(entry)
                        print(f"    + param_value  : {k} = {_normalise(v)}")
                    else:
                        print(f"    ~ already set  : {k} (skipped)")
            else:
                print("  No differences – removing reference only")

            # Do NOT append to new_param_files: this removes the autoware_launch ref
            node_changed = True

        if node_changed:
            node_entry["param_files"] = new_param_files
            node_entry["param_values"] = param_values
            changed = True

    if not changed:
        print("\nNo autoware_launch references found – nothing to do.")
        return False

    dest = output_path or parameter_set_path
    if dry_run:
        print(f"\n[dry-run] Would write to: {dest}")
        _dump_to_stream(data, sys.stdout)
    else:
        with dest.open("w") as f:
            _dump_to_stream(data, f)
        print(f"\nWritten: {dest}")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("parameter_set", type=Path, help="*.parameter_set.yaml to process")
    parser.add_argument(
        "--workspace-src",
        type=Path,
        default=None,
        help="Path to colcon workspace src/ (autodetected if omitted)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        metavar="FILE",
        help="Write result to FILE instead of modifying in place",
    )
    args = parser.parse_args()

    param_set = args.parameter_set.resolve()
    if not param_set.exists():
        sys.exit(f"ERROR: File not found: {param_set}")

    if args.workspace_src:
        ws_src = args.workspace_src.resolve()
    else:
        ws_src = _find_workspace_src(param_set)

    print(f"Parameter set : {param_set}")
    print(f"Workspace src : {ws_src}")

    process(param_set, ws_src, dry_run=args.dry_run, output_path=args.output)


if __name__ == "__main__":
    main()
