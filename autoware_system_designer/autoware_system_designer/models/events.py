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

from typing import List
import logging
from ..utils.naming import generate_unique_id

logger = logging.getLogger(__name__)

# classes for deployment
class Event:
    def __init__(self, name: str, namespace: List[str], is_process_event=False):
        self.name = name
        self.namespace = namespace
        self.type_list = [
            "on_input",
            "on_trigger",
            "once",
            "periodic",
            "to_trigger",
            "to_output",
        ]
        # on_input: activate the event when the input is received
        # on_trigger: activate the event when the trigger is activated
        # once: fulfill the condition if the input is received once
        # periodic: periodically activate this event
        self.type: str = None
        self.process_event: bool = is_process_event

        self.triggers: List["Event"] = []  # children triggers
        self.actions: List["Event"] = []  # event to trigger when this event is activated
        self.trigger_root_ids: List[str] = []  # trigger root ids

        self.frequency: float = None
        self.warn_rate: float = None
        self.error_rate: float = None
        self.timeout: float = None
        self.is_set: bool = False

    @property
    def unique_id(self):
        return generate_unique_id(self.namespace, self.name)

    def set_type(self, type_str):
        if type_str not in self.type_list:
            raise ValueError(f"Invalid event type: {type_str}")
        self.type = type_str
        logger.debug(f"Event '{self.unique_id}' set type '{type_str}'")

    def check_trigger_root_ids(self, trigger_root_id):
        if trigger_root_id in self.trigger_root_ids:
            return True
        else:
            self.trigger_root_ids.append(trigger_root_id)
            return False

    def add_trigger_event(self, event, vise_versa=True):
        if event.unique_id == self.unique_id:
            raise ValueError(f"Event cannot trigger itself: {self.unique_id}")
        for e in self.triggers:
            if e.unique_id == event.unique_id:
                return
        self.triggers.append(event)
        logger.debug(f"Event '{self.unique_id}' added trigger '{event.unique_id}'")
        if vise_versa:
            event.add_action_event(self, False)

    def add_action_event(self, event, vise_versa=True):
        if event.unique_id == self.unique_id:
            raise ValueError(f"Event cannot trigger itself: {self.unique_id}")
        for e in self.actions:
            if e.unique_id == event.unique_id:
                return
        self.actions.append(event)
        logger.debug(f"Event '{self.unique_id}' added action '{event.unique_id}'")
        if vise_versa:
            event.add_trigger_event(self, False)

    def determine_type(self, config_yaml):
        if len(config_yaml) == 0:
            raise ValueError("Config is empty")
        first_config = list(config_yaml)[0]
        if isinstance(first_config, str):
            type_key = first_config
            value = config_yaml.get(type_key)
        elif isinstance(first_config, dict):
            type_key = list(first_config.keys())[0]
            value = first_config[type_key]
        else:
            raise ValueError("Invalid config format for event type determination")
        return type_key, value

    def set_trigger(
        self,
        config_yaml,
        process_list: List["Event"],
        on_input_list: List["Event"],
    ):
        # get the config type
        config_key, config_value = self.determine_type(config_yaml)

        # convert to dict if the type is list and the size is 1
        if isinstance(config_yaml, list) and len(config_yaml) == 1:
            config_yaml = config_yaml[0]

        if config_key in self.type_list:
            # incoming event
            if config_key == "periodic":
                self.frequency = config_value
                self.is_set = True
            elif config_key == "once" and config_value is None:
                self.frequency = 0.0
                self.warn_rate = 0.0
                self.error_rate = 0.0
                self.timeout = 0.0
                self.is_set = True
            elif config_key == "on_input" or config_key == "once":
                self.condition_value = config_value
                # search the event in the on_input_list
                is_found = False
                for event in on_input_list:
                    if event.name == ("input_" + config_value):
                        self.add_trigger_event(event)
                        is_found = True
                        break
                # if not found, warn
                if not is_found:
                    raise ValueError(f"Input event not found: {config_value}")
            elif config_key == "on_trigger":
                self.condition_value = config_value
                # search the event in the process_list
                is_found = False
                for event in process_list:
                    if event.name == (config_value):
                        self.add_trigger_event(event)
                        is_found = True
                        break
                # if not found, warn
                if not is_found:
                    raise ValueError(f"Trigger event not found: {config_value}")
            else:
                raise ValueError(f"Invalid event type to set trigger: {config_key}")

            # set the topic monitor configurations, if available
            if "warn_rate" in config_yaml.keys():
                self.warn_rate = config_yaml.get("warn_rate")
            if "error_rate" in config_yaml.keys():
                self.error_rate = config_yaml.get("error_rate")
            if "timeout" in config_yaml.keys():
                self.timeout = config_yaml.get("timeout")

            logger.debug(
                f"Event '{self.unique_id}' configured as '{self.type}' ({config_key}); triggers={[t.unique_id for t in self.triggers]}"
            )
        else:
            raise ValueError(f"Invalid event type: {config_key}")

    def set_event_frequency(
        self,
        trigger_root_id: str,
        frequency: float,
        warn_rate: float,
        error_rate: float,
        timeout: float,
    ):
        if self.check_trigger_root_ids(trigger_root_id):
            logger.debug(f"Event '{self.unique_id}' already processed for root '{trigger_root_id}', skipping propagation")
            return

        if frequency is not None:
            if self.frequency is None or frequency > self.frequency:
                self.frequency = frequency
        if warn_rate is not None:
            if self.warn_rate is None or warn_rate > self.warn_rate:
                self.warn_rate = warn_rate
        if error_rate is not None:
            if self.error_rate is None or error_rate > self.error_rate:
                self.error_rate = error_rate
        if timeout is not None:
            if self.timeout is None or timeout > self.timeout:
                self.timeout = timeout

        self.is_set = True
        logger.debug(
            f"Event '{self.unique_id}' frequency set: freq={self.frequency}, warn={self.warn_rate}, error={self.error_rate}, timeout={self.timeout}"
        )
        for action in self.actions:
            action.set_event_frequency(trigger_root_id, frequency, warn_rate, error_rate, timeout)

    def set_frequency_tree(self):
        trigger_root_id = self.unique_id
        if self.type == "periodic" and self.is_set:
            # propagate the frequency to the children
            for action in self.actions:
                action.set_event_frequency(
                    trigger_root_id, self.frequency, self.warn_rate, self.error_rate, self.timeout
                )
        elif self.type == "once" and self.is_set:
            # propagate the frequency to the children
            for action in self.actions:
                action.set_event_frequency(trigger_root_id, 0.0, 0.0, 0.0, 0.0)
        else:
            pass


class EventChain(Event):
    def __init__(self, name: str, namespace: List[str] = [], is_process_event=True):
        super().__init__(name, namespace, is_process_event)
        self.chain_list = ["and", "or"]
        self.children: List[Event] = []

    def set_type(self, type_str):
        if type_str not in self.chain_list + self.type_list:
            raise ValueError(f"Invalid event chain type: {type_str}")
        self.type = type_str
        logger.debug(f"EventChain '{self.unique_id}' set type '{type_str}'")

    def set_chain(
        self,
        config_yaml,
        process_list: List[Event],
        on_input_list: List[Event],
        child_idx=0,
    ):
        logger.debug(f"EventChain '{self.unique_id}' parsing chain config: {config_yaml}")
        if isinstance(config_yaml, dict):
            config_key, config_value = self.determine_type(config_yaml)
            if config_key in self.type_list:
                self.set_type(config_key)
                self.set_trigger(config_yaml, process_list, on_input_list)
            elif config_key in self.chain_list:
                if len(config_value) == 1:
                    self.set_chain(config_value[0], process_list, on_input_list)
                else:
                    self.set_type(config_key)
                    for chain in config_value:
                        chain_key, chain_value = self.determine_type(chain)
                        if chain_key in ["periodic"] + self.chain_list:
                            event = EventChain(self.name + "_" + str(child_idx), self.namespace)
                            child_idx += 1
                            event.set_chain(chain, process_list, on_input_list)
                            self.add_trigger_event(event)
                            self.children.append(event)
                        elif chain_key in self.type_list:
                            self.set_trigger(chain, process_list, on_input_list)
                        else:
                            raise ValueError(f"Invalid trigger condition type: {chain_key}")
        elif isinstance(config_yaml, list):
            length = len(config_yaml)
            if length == 1:
                self.set_chain(config_yaml[0], process_list, on_input_list)
            else:
                config_dict = {"or": config_yaml}
                self.set_chain(config_dict, process_list, on_input_list)

    def get_children(self):
        # recursively get the children
        children_list = []
        for child in self.children:
            children_list += child.get_children()
        return children_list + self.children


class Process:
    def __init__(self, name: str, namespace: List[str], config_yaml: dict):
        self.name = name
        self.namespace = namespace
        self.config_yaml = config_yaml
        self.event: EventChain = EventChain(name, namespace)

    @property
    def unique_id(self):
        return generate_unique_id(self.namespace, "process", self.name)

    def set_condition(self, process_list, on_input_list):
        trigger_condition_config = self.config_yaml.get("trigger_conditions")
        logger.debug(f"Process '{self.unique_id}' setting trigger condition: {trigger_condition_config}")
        self.event.set_chain(trigger_condition_config, process_list, on_input_list)

    def set_outcomes(self, process_list, to_output_events):
        outcome_config = self.config_yaml.get("outcomes")
        for outcome in outcome_config:
            outcome_type = list(outcome.keys())[0]
            outcome_value = outcome[outcome_type]
            if outcome_type == "to_output":
                for event in to_output_events:
                    if event.name == ("output_" + outcome_value):
                        self.event.add_action_event(event)
                        break
                if self.event.actions == []:
                    raise ValueError(f"Output event not found: {outcome_value}")
            elif outcome_type == "to_trigger":
                for event in process_list:
                    if event.name == outcome_value:
                        self.event.add_action_event(event)
                        break
                if self.event.actions == []:
                    raise ValueError(f"Trigger event not found: {outcome_value}")
            elif outcome_type == "terminal":
                # end of event chain
                break
        logger.debug(
            f"Process '{self.unique_id}' outcomes configured: actions={[a.unique_id for a in self.event.actions]}"
        )

    def get_event_list(self):
        return self.event.get_children() + [self.event]


