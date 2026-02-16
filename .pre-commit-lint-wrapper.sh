#!/bin/bash
# System design format linting wrapper for pre-commit
# Handles only system design format files (*.node.yaml, *.module.yaml, *.system.yaml, *.parameter_set.yaml)
# YAML and Markdown linting are handled by separate pre-commit hooks that auto-install dependencies

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_DIR="${SCRIPT_DIR}/autoware_system_designer"
REPO_ROOT="${SCRIPT_DIR}"

# Set PYTHONPATH so Python can find the autoware_system_designer module
export PYTHONPATH="${PACKAGE_DIR}:${PYTHONPATH}"

# Process each file passed by pre-commit (only system design format files)
for file in "$@"; do
    # Convert to absolute path if relative
    if [[ "$file" != /* ]]; then
        file="${REPO_ROOT}/${file}"
    fi
    
    # System design format linting - need to be in package directory
    (cd "${PACKAGE_DIR}" && python3 -m autoware_system_designer.linter.run_lint --format human "$file") || exit 1
done
