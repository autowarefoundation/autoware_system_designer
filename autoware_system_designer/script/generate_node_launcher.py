# Copyright 2026 TIER IV, inc.
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

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = SCRIPT_DIR.parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))


from autoware_system_designer.common.exceptions import ValidationError  # noqa: E402
from autoware_system_designer.common.logging_utils import (  # noqa: E402
    configure_split_stream_logging,
)
from autoware_system_designer.generator.ros2_launcher.generate_node_launcher import (  # noqa: E402
    generate_node_launcher,
)


def run(node_yaml_paths: List[str], launch_file_dir: str) -> int:
    configure_split_stream_logging(
        level=logging.INFO,
        formatter=logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"),
    )
    logger = logging.getLogger(__name__)

    # Every file is attempted, so one run reports all invalid configs.
    failed = 0
    for node_yaml_path in node_yaml_paths:
        try:
            output_path = generate_node_launcher(node_yaml_path, launch_file_dir, strict_mode=True)
        except ValidationError as exc:
            logger.error(f"Invalid node config: {exc}")
            failed += 1
            continue
        except Exception as exc:
            logger.error(f"Failed to generate launcher for {node_yaml_path}: {exc}")
            failed += 1
            continue
        logger.info(f"Saved launcher to: {output_path}")

    return 1 if failed else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate ROS 2 launch XML files for node config YAMLs")
    parser.add_argument("node_yaml", nargs="+", help="Path(s) to '<Name>.node.yaml'")
    parser.add_argument("output_dir", help="Directory to write '<name>.launch.xml'")

    args = parser.parse_args(argv)
    return run(args.node_yaml, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
