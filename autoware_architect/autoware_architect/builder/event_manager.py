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

import logging
from typing import List, TYPE_CHECKING

from ..models.events import Event, Process

if TYPE_CHECKING:
    from .instances import Instance

logger = logging.getLogger(__name__)


class EventManager:
    """Manages event and process operations for Instance objects."""
    
    def __init__(self, instance: 'Instance'):
        self.instance = instance
        
        # processes
        self.processes: List[Process] = []
        self.event_list: List[Event] = []

    def initialize_node_processes(self):
        """Initialize processes for node entity during node configuration."""
        if self.instance.entity_type != "node":
            return
            
        # connect port events and the process events
        on_input_events = self.instance.link_manager.get_input_events()
        to_output_events = self.instance.link_manager.get_output_events()

        # parse processes and get trigger conditions and output conditions
        for process_config in self.instance.configuration.processes:
            name = process_config.get("name")
            self.processes.append(Process(name, self.instance.namespace, process_config))

        # set the process events
        process_event_list = [process.event for process in self.processes]
        if len(process_event_list) == 0:
            # process configuration is not found
            logger.warning(f"No process found in {self.instance.name}, at {self.instance.configuration.file_path}")
            return
        for process in self.processes:
            process.set_condition(process_event_list, on_input_events)
            process.set_outcomes(process_event_list, to_output_events)

        # set the process events
        process_event_list = []
        for process in self.processes:
            process_event_list.extend(process.get_event_list())
        self.event_list = process_event_list

    def set_event_tree(self):
        """Set up the event tree for the current instance."""
        # trigger the event tree from the current instance
        # in case of module, event_list is empty
        for event in self.event_list:
            event.set_frequency_tree()
        # recursive call for children
        # in case of node, children is empty
        for child in self.instance.children.values():
            child.event_manager.set_event_tree()

    def get_all_events(self):
        """Get all events."""
        return self.event_list
