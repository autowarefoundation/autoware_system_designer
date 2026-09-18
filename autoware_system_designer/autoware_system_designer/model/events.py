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
from collections import deque
from dataclasses import dataclass, field
from typing import ClassVar, Dict, Iterable, List, Optional, Tuple

from autoware_system_designer.common.naming import generate_unique_id

logger = logging.getLogger(__name__)


# classes for deployment
@dataclass(init=False, eq=False, repr=False)
class Event:
    # on_input: activate the event when the input is received
    # on_trigger: activate the event when the trigger is activated
    # once: fulfill the condition if the input is received once
    # periodic: periodically activate this event
    # queue: data parked by a process for whichever process reads it next
    type_list: ClassVar[List[str]] = [
        "on_input",
        "on_trigger",
        "once",
        "periodic",
        "to_trigger",
        "to_output",
        "queue",
    ]

    name: str
    namespace: List[str] = field(metadata={"exclude": True})
    type: Optional[str] = None
    process_event: bool = False
    # children triggers
    triggers: List["Event"] = field(default_factory=list, metadata={"ref": True, "alias": "trigger_ids"})
    # triggers that only gate this event: satisfied for good once they arrive, so they set no rate
    latches: List["Event"] = field(default_factory=list, metadata={"ref": True, "alias": "latch_ids"})
    # events to trigger when this event is activated
    actions: List["Event"] = field(default_factory=list, metadata={"ref": True, "alias": "action_ids"})
    # queues this event reads when it runs; a read neither fires it nor paces it
    reads: List["Event"] = field(default_factory=list, metadata={"ref": True, "alias": "read_ids"})
    frequency: Optional[float] = None
    warn_rate: Optional[float] = None
    error_rate: Optional[float] = None
    timeout: Optional[float] = None
    is_set: bool = field(default=False, metadata={"exclude": True})

    __serde_computed__: ClassVar[tuple] = (("unique_id", "unique_id"),)

    def __init__(self, name: str, namespace: List[str], is_process_event=False):
        self.name = name
        self.namespace = namespace
        self.type = None
        self.process_event = is_process_event

        self.triggers = []
        self.latches = []
        self.actions = []
        self.reads = []

        self.frequency = None
        self.warn_rate = None
        self.error_rate = None
        self.timeout = None
        self.is_set = False

    @property
    def unique_id(self):
        return generate_unique_id(self.namespace, "event", self.name)

    @property
    def is_port_event(self):
        return False

    @property
    def is_clock(self):
        """A condition that carries its own rate: `periodic` repeats at it, `once` never repeats."""
        return self.type in ("periodic", "once")

    @property
    def pacing_triggers(self) -> List["Event"]:
        """The triggers that set the rate of this event; a latch only gates it."""
        latch_ids = {latch.unique_id for latch in self.latches}
        return [trigger for trigger in self.triggers if trigger.unique_id not in latch_ids]

    @staticmethod
    def _as_rate(key: str, value) -> float:
        """Rate fields hold Hz or seconds as floats; substitutions are resolved before the config arrives."""
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"'{key}' must be a number, got {value!r}")
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"'{key}' must be a number, got {value!r}") from exc

    def set_type(self, type_str):
        if type_str not in self.type_list:
            raise ValueError(f"Invalid event type: {type_str}")
        self.type = type_str
        logger.debug(f"Event '{self.unique_id}' set type '{type_str}'")

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

    def add_latch_event(self, event: "Event"):
        self.add_trigger_event(event)
        for e in self.latches:
            if e.unique_id == event.unique_id:
                return
        self.latches.append(event)
        logger.debug(f"Event '{self.unique_id}' added latch '{event.unique_id}'")

    def add_read_event(self, queue: "QueueEvent"):
        if queue.unique_id == self.unique_id:
            raise ValueError(f"Event cannot read itself: {self.unique_id}")
        for e in self.reads:
            if e.unique_id == queue.unique_id:
                return
        self.reads.append(queue)
        queue.add_reader_event(self)
        logger.debug(f"Event '{self.unique_id}' added read '{queue.unique_id}'")

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
                self.frequency = self._as_rate("periodic", config_value)
                self.is_set = True
            elif config_key == "once" and config_value is None:
                # a bare `once` folded into a chain only gates it; alone it is the condition itself
                if self.type == "once":
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
                        if config_key == "once" and self.type != "once":
                            self.add_latch_event(event)
                        else:
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
                self.warn_rate = self._as_rate("warn_rate", config_yaml.get("warn_rate"))
            if "error_rate" in config_yaml.keys():
                self.error_rate = self._as_rate("error_rate", config_yaml.get("error_rate"))
            if "timeout" in config_yaml.keys():
                self.timeout = self._as_rate("timeout", config_yaml.get("timeout"))

            logger.debug(
                f"Event '{self.unique_id}' configured as '{self.type}' ({config_key}); triggers={[t.unique_id for t in self.triggers]}"
            )
        else:
            raise ValueError(f"Invalid event type: {config_key}")


@dataclass(init=False, eq=False, repr=False)
class QueueEvent(Event):
    """Node-owned buffer between a process that fills it and the processes that read it.

    Filling is a trigger relation, so the fill rate propagates to the queue and stops there;
    reading is recorded on both ends and carries no rate.
    """

    # events reading this queue when they run
    readers: List[Event] = field(default_factory=list, metadata={"ref": True, "alias": "reader_ids"})

    def __init__(self, name: str, namespace: List[str]):
        super().__init__(name, namespace)
        self.readers = []
        self.set_type("queue")

    @property
    def unique_id(self):
        return generate_unique_id(self.namespace, "queue", self.name)

    def add_reader_event(self, event: Event):
        for e in self.readers:
            if e.unique_id == event.unique_id:
                return
        self.readers.append(event)


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

    def set_outcomes(self, process_list, to_output_events, queues: Dict[str, QueueEvent]):
        """Wire the process to what it produces; a `to_queue` outcome declares the queue on first use."""
        outcome_config = self.config_yaml.get("outcomes")
        for outcome in outcome_config:
            outcome_type = list(outcome.keys())[0]
            outcome_value = outcome[outcome_type]
            if outcome_type == "to_output":
                target = next((e for e in to_output_events if e.name == ("output_" + outcome_value)), None)
                if target is None:
                    raise ValueError(f"Output event not found: {outcome_value}")
                self.event.add_action_event(target)
            elif outcome_type == "to_trigger":
                target = next((e for e in process_list if e.name == outcome_value), None)
                if target is None:
                    raise ValueError(f"Trigger event not found: {outcome_value}")
                self.event.add_action_event(target)
            elif outcome_type == "to_queue":
                if not isinstance(outcome_value, str) or not outcome_value:
                    raise ValueError(f"'to_queue' must name a queue, got {outcome_value!r}")
                queue = queues.get(outcome_value)
                if queue is None:
                    queue = QueueEvent(outcome_value, self.namespace)
                    queues[outcome_value] = queue
                self.event.add_action_event(queue)
            elif outcome_type == "terminal":
                # end of event chain
                break
            else:
                raise ValueError(f"Invalid outcome type: {outcome_type}")
        logger.debug(
            f"Process '{self.unique_id}' outcomes configured: actions={[a.unique_id for a in self.event.actions]}"
        )

    def set_reads(self, queues: Dict[str, QueueEvent]):
        """Bind the queues the process reads; every queue must be filled by a `to_queue` of this node."""
        for read in self.config_yaml.get("reads") or []:
            if not isinstance(read, dict) or len(read) != 1:
                raise ValueError(f"Invalid read entry: {read!r}")
            read_type, read_value = next(iter(read.items()))
            if read_type != "from_queue":
                raise ValueError(f"Invalid read type: {read_type}")
            queue = queues.get(read_value)
            if queue is None:
                raise ValueError(
                    f"Queue not found: {read_value} (no process of this node has 'to_queue: {read_value}')"
                )
            self.event.add_read_event(queue)

    def get_event_list(self):
        return self.event.get_children() + [self.event]


RateProfile = Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]


def _profile(event: Event) -> RateProfile:
    return (event.frequency, event.warn_rate, event.error_rate, event.timeout)


def _inherit(values: List[Optional[float]], slowest: bool) -> Optional[float]:
    """The value an event takes from its pacing triggers; a `0.0` rate is a one-shot and paces nothing."""
    known = [value for value in values if value is not None]
    if not known:
        return None
    if not slowest:
        return max(known)
    paced = [value for value in known if value]
    return min(paced) if paced else 0.0


def _settle(event: Event, declared: RateProfile) -> bool:
    """Recompute the rate profile of one event; True when it moved."""
    before = _profile(event)
    if event.is_clock:
        frequency = declared[0] if declared[0] is not None else 0.0
        profile: RateProfile = (frequency, declared[1], declared[2], declared[3])
    else:
        # an `and` fires no faster than its slowest condition, every other event as fast as its
        # quickest; `timeout` is a duration, so the slowest condition allows the longest wait
        slowest = event.type == "and"
        triggers = event.pacing_triggers
        inherited = (
            _inherit([trigger.frequency for trigger in triggers], slowest),
            _inherit([trigger.warn_rate for trigger in triggers], slowest),
            _inherit([trigger.error_rate for trigger in triggers], slowest),
            _inherit([trigger.timeout for trigger in triggers], False),
        )
        profile = tuple(own if own is not None else value for own, value in zip(declared, inherited))
    event.frequency, event.warn_rate, event.error_rate, event.timeout = profile
    return profile != before


def resolve_event_rates(events: Iterable[Event], max_visits: int = 8) -> None:
    """Settle the rate of every event from the clocks that reach it.

    A clock keeps the rate it declares; every other event takes the rate of the conditions that
    pace it, along the trigger edges. Chains that loop settle by revisiting, bounded per event.
    """
    events = list(events)
    declared = {event.unique_id: _profile(event) for event in events}
    queue = deque(events)
    queued = {event.unique_id for event in events}
    visits: Dict[str, int] = {}
    while queue:
        event = queue.popleft()
        queued.discard(event.unique_id)
        if not _settle(event, declared[event.unique_id]):
            continue
        visits[event.unique_id] = visits.get(event.unique_id, 0) + 1
        if visits[event.unique_id] > max_visits:
            logger.warning(f"Event '{event.unique_id}' rate does not settle: the chain it sits on mixes rates")
            continue
        logger.debug(f"Event '{event.unique_id}' rate settled: freq={event.frequency}")
        for action in event.actions:
            if action.unique_id not in queued:
                queue.append(action)
                queued.add(action.unique_id)
