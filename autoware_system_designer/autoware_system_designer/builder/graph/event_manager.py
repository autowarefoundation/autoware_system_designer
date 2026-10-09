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

import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List

from autoware_system_designer.common.exceptions import NodeConfigurationError
from autoware_system_designer.common.source_location import format_source, source_from_config
from autoware_system_designer.model.events import Event, Process, QueueEvent, resolve_event_rates

if TYPE_CHECKING:
    from autoware_system_designer.builder.instances.instances import Instance

logger = logging.getLogger(__name__)


def _resolve_substitutions(value: Any, resolve: Callable[[str], str]) -> Any:
    """Apply a string resolver to every string of a config tree."""
    if isinstance(value, str):
        return resolve(value)
    if isinstance(value, dict):
        return {key: _resolve_substitutions(item, resolve) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_substitutions(item, resolve) for item in value]
    return value


class EventManager:
    """Manages event and process operations for Instance objects."""

    def __init__(self, instance: "Instance"):
        self.instance = instance

        # processes
        self.processes: List[Process] = []
        # node-owned queues, keyed by the name `to_queue` gives them
        self.queues: Dict[str, QueueEvent] = {}
        self.event_list: List[Event] = []

    def initialize_processes(self):
        """Build the processes of every node in the subtree.

        Runs after the tree's parameters are final and before any rate propagates,
        so `${parameter ...}` in a trigger reads the node's effective value and
        downstream process events exist when an upstream rate reaches them.
        """
        self.initialize_node_processes()
        for child in self.instance.children.values():
            child.event_manager.initialize_processes()

    def initialize_node_processes(self):
        """Initialize processes for node entity; substitutions resolve against the node's parameters."""
        # node-group containers are synthesized nodes without a design file
        if self.instance.entity_type != "node" or self.instance.configuration is None:
            return

        # connect port events and the process events
        on_input_events = self.instance.link_manager.get_input_events()
        to_output_events = self.instance.link_manager.get_output_events()

        # parse processes and get trigger conditions and output conditions
        sources = []
        for idx, process_config in enumerate(self.instance.configuration.processes):
            src = source_from_config(self.instance.configuration, f"/processes/{idx}")
            process_config = _resolve_substitutions(
                process_config, lambda text: self.instance.parameter_manager.resolve_substitutions(text, source=src)
            )
            name = process_config.get("name")
            self.processes.append(Process(name, self.instance.resolved_path, process_config))
            sources.append(src)

        # set the process events
        process_event_list = [process.event for process in self.processes]
        if len(process_event_list) == 0:
            # process configuration is not found
            src = source_from_config(self.instance.configuration, "/processes")
            logger.warning(f"No process found in {self.instance.name}{format_source(src)}")
            return
        for process, src in zip(self.processes, sources):
            try:
                process.set_condition(process_event_list, on_input_events)
                process.set_outcomes(process_event_list, to_output_events, self.queues)
            except ValueError as exc:
                raise NodeConfigurationError(f"{exc}{format_source(src)}") from exc
        # reads resolve after every outcome, so a queue may be filled by a later process
        for process, src in zip(self.processes, sources):
            try:
                process.set_reads(self.queues)
            except ValueError as exc:
                raise NodeConfigurationError(f"{exc}{format_source(src)}") from exc

        # set the process events
        process_event_list = []
        for process in self.processes:
            process_event_list.extend(process.get_event_list())
        self.event_list = process_event_list + list(self.queues.values())

    def set_event_tree(self):
        """Settle the rates of the subtree; the graph is solved as a whole, since a rate crosses
        node boundaries along the links."""
        resolve_event_rates(self.collect_event_graph())

    def collect_event_graph(self) -> List[Event]:
        """Every event of the subtree, closed over the trigger and action edges the links add."""
        collected: Dict[str, Event] = {}
        # in case of module, event_list is empty; in case of node, children is empty
        stack = list(self.event_list)
        for child in self.instance.children.values():
            stack.extend(child.event_manager.collect_event_graph())
        while stack:
            event = stack.pop()
            if event.unique_id in collected:
                continue
            collected[event.unique_id] = event
            stack.extend(event.triggers)
            stack.extend(event.actions)
        return list(collected.values())

    def get_all_events(self):
        """Get all events."""
        return self.event_list
