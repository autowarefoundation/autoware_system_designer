#!/usr/bin/env python3
# Copyright 2025 TIER IV, inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CLI entry point for linting autoware_system_design_format files."""

import argparse
import sys
from pathlib import Path
from typing import List

try:
    from . import lint_files, LintResult
except ImportError:  # pragma: no cover
    # Allow direct execution: `python path/to/run_lint.py ...`
    SCRIPT_DIR = Path(__file__).resolve().parent
    REPO_ROOT = SCRIPT_DIR.parent.parent
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from autoware_system_designer.linter import lint_files, LintResult


def find_yaml_files(paths: List[str]) -> List[Path]:
    """Find all autoware_system_design_format YAML files in given paths."""
    yaml_files = []
    entity_extensions = ['.node.yaml', '.module.yaml', '.system.yaml', '.parameter_set.yaml']
    
    for path_str in paths:
        path = Path(path_str)
        
        if not path.exists():
            print(f"Warning: Path does not exist: {path}", file=sys.stderr)
            continue
        
        if path.is_file():
            # Check if it's a valid entity file
            if any(path.name.endswith(ext) for ext in entity_extensions):
                yaml_files.append(path)
            else:
                print(f"Warning: File does not match entity file pattern: {path}", file=sys.stderr)
        elif path.is_dir():
            # Recursively find all entity YAML files
            for ext in entity_extensions:
                yaml_files.extend(path.rglob(f'*{ext}'))
        else:
            print(f"Warning: Path is neither file nor directory: {path}", file=sys.stderr)
    
    return sorted(set(yaml_files))


def main(argv: List[str] | None = None) -> None:
    """Main entry point for the linter CLI."""
    parser = argparse.ArgumentParser(
        description='Lint autoware_system_design_format YAML files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'paths',
        nargs='*',
        default=None,
        help='File paths or directories to lint (default: current directory)',
    )
    parser.add_argument(
        '--format',
        choices=['human', 'json', 'github-actions'],
        default='human',
        help='Output format (default: human)',
    )
    
    args = parser.parse_args(argv)

    if not args.paths:
        args.paths = ['.']
    
    # Find all YAML files
    yaml_files = find_yaml_files(args.paths)
    
    if not yaml_files:
        print("No autoware_system_design_format files found.", file=sys.stderr)
        sys.exit(1)
    
    # Lint all files
    results = lint_files(yaml_files)
    
    # Print results in requested format
    if args.format == 'json':
        import json
        output = {
            'files': len(results),
            'errors': sum(len(r.errors) for r in results),
            'warnings': sum(len(r.warnings) for r in results),
            'results': [
                {
                    'file': str(r.file_path),
                    'errors': r.errors,
                    'warnings': r.warnings,
                }
                for r in results
            ]
        }
        print(json.dumps(output, indent=2))
    elif args.format == 'github-actions':
        for result in results:
            for error in result.errors:
                print(f"::error file={result.file_path},line={error.get('line', 1)}::{error['message']}")
            for warning in result.warnings:
                print(f"::warning file={result.file_path},line={warning.get('line', 1)}::{warning['message']}")
    else:  # human-readable
        for result in results:
            if result.errors or result.warnings:
                print(f"\n{result.file_path}:")
                for error in result.errors:
                    line_info = f":{error.get('line', '?')}" if 'line' in error else ""
                    print(f"  ERROR{line_info}: {error['message']}")
                for warning in result.warnings:
                    line_info = f":{warning.get('line', '?')}" if 'line' in warning else ""
                    print(f"  WARNING{line_info}: {warning['message']}")
    
    # Exit with error code if any errors found
    total_errors = sum(len(r.errors) for r in results)
    if total_errors > 0:
        sys.exit(1)
    if args.format == 'human':
        print("Lint succeeded with no errors.")
    sys.exit(0)


if __name__ == '__main__':
    main()

