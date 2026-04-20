"""Live ROS 2 graph snapshot via topology-analyzer.

Calls ros2_graph_snapshot.py as a subprocess so that rclpy initialisation
does not interfere with the caller process (which typically runs without ROS).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SNAPSHOT_SCRIPT = (
    Path(__file__).parent.parent.parent / "topology-analyzer" / "ros2_graph_snapshot.py"
)


def capture_live_snapshot(
    output_path: Path,
    spin_seconds: float = 3.0,
    params: str = "names",
) -> Path:
    """Capture a live ROS 2 graph snapshot using topology-analyzer.

    Requires a running ROS 2 system and a sourced ROS 2 environment.
    The snapshot script is executed as a subprocess so that rclpy does not
    affect the caller's interpreter state.

    Args:
        output_path: Destination path for the graph.json file.
        spin_seconds: Seconds to wait for node discovery (3–5 recommended).
        params: Parameter collection depth — ``'none'``, ``'names'``, or ``'values'``.

    Returns:
        Path to the written graph.json.

    Raises:
        FileNotFoundError: If topology-analyzer is not found next to this tool.
        subprocess.CalledProcessError: If the snapshot script exits non-zero.
    """
    if not _SNAPSHOT_SCRIPT.exists():
        raise FileNotFoundError(
            f"topology-analyzer snapshot script not found: {_SNAPSHOT_SCRIPT}\n"
            "Ensure topology-analyzer/ is present alongside system-config-generator/."
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            sys.executable,
            str(_SNAPSHOT_SCRIPT),
            "--out", str(output_path),
            "--spin-seconds", str(spin_seconds),
            "--params", params,
        ],
        check=True,
    )
    return output_path
