#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path
from typing import List


SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = SCRIPT_DIR.parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from autoware_system_designer.linter.run_lint import main as run_lint_main  # noqa: E402


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

    lint_argv = ["--format", args.format]
    lint_argv.extend(str(p) for p in lint_targets)

    run_lint_main(lint_argv)


if __name__ == "__main__":
    main()
