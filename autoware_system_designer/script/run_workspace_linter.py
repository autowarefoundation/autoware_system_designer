#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path
from typing import List


SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = SCRIPT_DIR.parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from autoware_system_designer.linter import lint_files  # noqa: E402


ENTITY_EXTENSIONS = [
    ".node.yaml",
    ".module.yaml",
    ".system.yaml",
    ".parameter_set.yaml",
]


def find_yaml_files(paths: List[Path]) -> List[Path]:
    """Find all autoware_system_design_format YAML files in given paths."""
    yaml_files: List[Path] = []

    for path in paths:
        if not path.exists():
            print(f"Warning: Path does not exist: {path}", file=sys.stderr)
            continue

        if path.is_file():
            if any(path.name.endswith(ext) for ext in ENTITY_EXTENSIONS):
                yaml_files.append(path)
            else:
                print(
                    f"Warning: File does not match entity file pattern: {path}",
                    file=sys.stderr,
                )
        elif path.is_dir():
            for ext in ENTITY_EXTENSIONS:
                yaml_files.extend(path.rglob(f"*{ext}"))
        else:
            print(f"Warning: Path is neither file nor directory: {path}", file=sys.stderr)

    return sorted(set(yaml_files))


def resolve_default_paths(workspace: Path) -> List[Path]:
    """Resolve default config search path from launch path."""
    return [workspace]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run autoware_system_designer linter for a workspace",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "workspace",
        nargs="?",
        default=".",
        help="Workspace root (default: current directory)",
    )
    parser.add_argument(
        "--paths",
        nargs="*",
        default=None,
        help="Optional explicit paths to lint (files or directories)",
    )
    parser.add_argument(
        "--format",
        choices=["human", "json", "github-actions"],
        default="human",
        help="Output format (default: human)",
    )

    args = parser.parse_args()

    workspace_arg = args.workspace or "."
    workspace = Path(workspace_arg).resolve()
    if args.paths:
        lint_targets = [Path(p).resolve() for p in args.paths]
    else:
        lint_targets = resolve_default_paths(workspace)

    if not lint_targets:
        print("No lint targets found.", file=sys.stderr)
        sys.exit(1)

    yaml_files = find_yaml_files(lint_targets)
    if not yaml_files:
        print("No autoware_system_design_format files found.", file=sys.stderr)
        sys.exit(1)

    results = lint_files(yaml_files)

    if args.format == "json":
        import json

        output = {
            "files": len(results),
            "errors": sum(len(r.errors) for r in results),
            "warnings": sum(len(r.warnings) for r in results),
            "results": [
                {
                    "file": str(r.file_path),
                    "errors": r.errors,
                    "warnings": r.warnings,
                }
                for r in results
            ],
        }
        print(json.dumps(output, indent=2))
    elif args.format == "github-actions":
        for result in results:
            for error in result.errors:
                print(
                    f"::error file={result.file_path},line={error.get('line', 1)}::"
                    f"{error['message']}"
                )
            for warning in result.warnings:
                print(
                    f"::warning file={result.file_path},line={warning.get('line', 1)}::"
                    f"{warning['message']}"
                )
    else:
        for result in results:
            if result.errors or result.warnings:
                print(f"\n{result.file_path}:")
                for error in result.errors:
                    line_info = f":{error.get('line', '?')}" if "line" in error else ""
                    print(f"  ERROR{line_info}: {error['message']}")
                for warning in result.warnings:
                    line_info = f":{warning.get('line', '?')}" if "line" in warning else ""
                    print(f"  WARNING{line_info}: {warning['message']}")

    total_errors = sum(len(r.errors) for r in results)
    if total_errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
