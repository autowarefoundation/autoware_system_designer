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

"""CLI entry: analyze a kept trace directory against a system_structure JSON offline."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from ._impl.measure.clock import CLOCK_AUTO, CLOCK_CHOICES
from ._impl.measure.node_graph import NodeGraph
from ._impl.measure.node_stats import NS_PER_S
from ._impl.measure.session import analyze_traces, default_latency_out
from .system_runner import _resolve_structure_paths

logger = logging.getLogger("autoware_system_designer")


def _parse_time(text: Optional[str]) -> Optional[int]:
    """Epoch seconds or an ISO-8601 timestamp, as ns; None passes through."""
    if text is None:
        return None
    try:
        return int(float(text) * NS_PER_S)
    except ValueError:
        pass
    stamp = datetime.fromisoformat(text)
    return int(stamp.timestamp() * NS_PER_S)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a runtime trace directory into a latency file.")
    parser.add_argument("trace_dir", type=Path, help="Directory of <pid>.trace / <pid>.names files")
    parser.add_argument("json_file", type=Path, help="system_structure JSON the traced system was launched from")
    parser.add_argument(
        "-o",
        "--latency-out",
        type=Path,
        default=None,
        help="Output latency file (default: the export's visualization/web/data/, else <trace_dir>/../latency/)",
    )
    parser.add_argument("--ecu", default=None, help="Restrict the design to nodes of this compute unit")
    parser.add_argument("--window-start", default=None, help="Epoch seconds or ISO time; default: first record")
    parser.add_argument("--window-end", default=None, help="Epoch seconds or ISO time; default: last record")
    parser.add_argument("--settle", type=float, default=0.0, help="Seconds skipped after the window start")
    parser.add_argument("--probe", dest="probe", action="store_true", default=None, help="Record that probes were on")
    parser.add_argument("--no-probe", dest="probe", action="store_false", help="Record that probes were off")
    parser.add_argument(
        "--clock",
        default=CLOCK_AUTO,
        choices=CLOCK_CHOICES,
        help="Time base of durations and rates: ros when the traced processes ran on /clock "
        "(use_sim_time), wall otherwise; auto picks by the trace (default)",
    )
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level), format="%(message)s")

    with open(args.json_file) as handle:
        data = json.load(handle)
    try:
        data = _resolve_structure_paths(data, str(args.json_file))
    except RuntimeError as exc:
        logger.warning("%s", exc)
    graph = NodeGraph.from_system_structure(data, ecu=args.ecu)
    mode = graph.mode or args.json_file.stem

    latency_out = args.latency_out or default_latency_out(mode, args.json_file, args.trace_dir.resolve().parent)

    window_start = _parse_time(args.window_start)
    if args.settle and window_start is not None:
        window_start += int(args.settle * NS_PER_S)
    elif args.settle:
        from ._impl.measure.trace_reader import read_trace_dir

        span_start, _ = read_trace_dir(args.trace_dir).time_span()
        window_start = span_start + int(args.settle * NS_PER_S)

    result = analyze_traces(
        args.trace_dir,
        graph,
        window_start_ns=window_start,
        window_end_ns=_parse_time(args.window_end),
        mode=mode,
        probe=args.probe,
        latency_out=latency_out,
        clock=args.clock,
    )
    run = result["run"]
    print(
        f"{run['window_s']} s window ({run['clock']['base']} time), {run['processes']} processes, "
        f"{len(result['nodes'])} nodes, "
        f"{len(result['links'])} links, {len(result['chains'])} chains"
    )
    print(f"latency file: {latency_out}")
    sys.exit(0)


if __name__ == "__main__":
    main()
