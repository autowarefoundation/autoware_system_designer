#!/bin/bash
# System design format linting wrapper for pre-commit
# Handles only system design format files (*.node.yaml, *.module.yaml, *.system.yaml, *.parameter_set.yaml)
# YAML and Markdown linting are handled by separate pre-commit hooks that auto-install dependencies

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_DIR="${SCRIPT_DIR}/autoware_system_designer"
REPO_ROOT="${SCRIPT_DIR}"

# Add package to PYTHONPATH to run from source
export PYTHONPATH="${PACKAGE_DIR}:${PYTHONPATH}"

# Process each file passed by pre-commit (only system design format files)
EXIT_CODE=0
for file in "$@"; do
    # Convert to absolute path if relative
    if [[ "$file" != /* ]]; then
        file="${REPO_ROOT}/${file}"
    fi
    
    # Run the linter using the source code
    python3 -m autoware_system_designer.linter.run_lint --format human "$file" || EXIT_CODE=1
done

exit $EXIT_CODE
