#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

# Add path to find autoware_architect if not installed
# This script is in src/architecture/autoware_architect/script/
# autoware_architect package is in src/architecture/autoware_architect/
current_dir = Path(__file__).resolve().parent
architect_root = current_dir.parent
if str(architect_root) not in sys.path:
    sys.path.insert(0, str(architect_root))

try:
    from autoware_architect.visualization.visualization_index import update_index
except ImportError:
    # Fallback if path setup failed or running from installed location
    try:
        from autoware_architect.visualization.visualization_index import update_index
    except ImportError:
        print("Error: Could not import autoware_architect.visualization.visualization_index")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate index of Autoware architecture visualizations.")
    parser.add_argument("--install-dir", default="install", help="Path to the install directory")
    args = parser.parse_args()

    print(f"Updating visualization index in {args.install_dir}...")
    update_index(args.install_dir)
    print("Done.")
