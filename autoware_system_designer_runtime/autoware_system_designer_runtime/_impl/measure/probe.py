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

"""Probe subscriptions that make intra-process-only publishes visible to the tracer.

A publisher whose every matched reader is intra-process skips ``rcl_publish``.
One extra reader with a QoS that matches any publisher makes it call
``rcl_publish`` for every message, so the tracer records it. The probe process
is the runtime itself, which is never traced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .node_graph import NodeGraph

logger = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    attached: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def probe_topics(graph: NodeGraph) -> list[tuple[str, str]]:
    return graph.probe_topics()


async def attach_probes(worker, topics: list[tuple[str, str]]) -> ProbeResult:
    result = ProbeResult()
    for topic, msg_type in topics:
        try:
            await worker.add_probe(topic, msg_type)
        except Exception as exc:  # noqa: BLE001
            result.failed[topic] = f"{type(exc).__name__}: {exc}"
            logger.warning("probe for %s not attached: %s", topic, result.failed[topic])
        else:
            result.attached.append(topic)
    return result


async def detach_probes(worker) -> None:
    try:
        await worker.remove_probes()
    except Exception as exc:  # noqa: BLE001
        logger.warning("probe removal failed: %s", exc)
