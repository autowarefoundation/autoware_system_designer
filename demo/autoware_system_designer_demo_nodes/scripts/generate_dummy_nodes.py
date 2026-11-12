#!/usr/bin/env python3
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

"""Generate the dummy node classes of the node designs.

Each design becomes one ``DummyNode`` subclass registered under the design's plugin name. The
design's processes become firing rules:

- ``periodic`` fires at its rate, ``on_input`` on each message, ``once: <input>`` on the first
  message, ``once`` without an input at start.
- Conditions of a list or an ``or`` fire independently.
- In an ``and`` the periodic conditions fire, or the input conditions when there is none, once
  every other condition has received a message.
- ``to_output`` publishes, ``to_trigger`` and ``on_trigger`` run the triggered process.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Tuple

import yaml


@dataclass
class Rule:
    """One way a process fires: by a timer, an input, or at start."""

    kind: str  # "timer" | "input" | "start"
    rate: float = 0.0
    source: str = ""
    once: bool = False
    gates: List[List[str]] = field(default_factory=list)


def snake(name: str) -> str:
    name = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()


def cpp_type(interface: str) -> Tuple[str, str]:
    """Return the header and the C++ type of ``package/msg/Name``."""
    package, kind, name = interface.split("/")
    return f"{package}/{kind}/{snake(name)}.hpp", f"{package}::{kind}::{name}"


def quoted(value: str) -> str:
    return json.dumps(value)


def condition_rules(condition: Any) -> Tuple[List[Rule], List[List[str]]]:
    """Return the rules a condition fires by, and the inputs it waits for as a gate."""
    if not isinstance(condition, dict):
        return [], []
    if "periodic" in condition:
        return [Rule("timer", rate=float(condition["periodic"]))], []
    if "on_input" in condition:
        name = condition["on_input"]
        return [Rule("input", source=name)], [[name]]
    if "once" in condition:
        name = condition["once"]
        if not name:
            return [Rule("start")], []
        return [Rule("input", source=name, once=True)], [[name]]
    if "or" in condition:
        rules: List[Rule] = []
        inputs: List[str] = []
        for child in condition["or"] or []:
            child_rules, child_gates = condition_rules(child)
            rules += child_rules
            inputs += [name for group in child_gates for name in group]
        return rules, [inputs] if inputs else []
    if "and" in condition:
        children = [condition_rules(child) for child in condition["and"] or []]
        periodic = [any(rule.kind == "timer" for rule in rules) for rules, _ in children]
        on_input = [any(rule.kind == "input" and not rule.once for rule in rules) for rules, _ in children]
        firing = periodic if any(periodic) else on_input if any(on_input) else [True] * len(children)
        rules = []
        for index, ((child_rules, _), fires) in enumerate(zip(children, firing)):
            if not fires:
                continue
            others = [group for other, (_, gates) in enumerate(children) if other != index for group in gates]
            for rule in child_rules:
                rule.gates += others
            rules += child_rules
        return rules, [group for _, child_gates in children for group in child_gates]
    return [], []


def rule_expression(process: str, rule: Rule) -> str:
    gates = ", ".join("{" + ", ".join(quoted(name) for name in group) + "}" for group in rule.gates)
    return f"Rule{{{quoted(process)}, {{{gates}}}, {'true' if rule.once else 'false'}, false}}"


def qos_expression(port: dict, subscriber: bool) -> str:
    qos = port.get("qos") or {}
    reliability = quoted(str(qos.get("reliability", "")))
    durability = quoted(str(qos.get("durability", "")))
    return f"port_qos({reliability}, {durability}, {'true' if subscriber else 'false'})"


def generate_class(design: dict, path: Path, headers: set) -> Tuple[str, str, str]:
    """Return the plugin, the executable and the class source of one design."""
    launch = design.get("launch") or {}
    plugin, executable = launch.get("plugin"), launch.get("executable")
    if not plugin or not executable:
        raise ValueError(f"{path}: a dummy node design needs launch.plugin and launch.executable")
    namespace, _, class_name = plugin.rpartition("::")

    body: List[str] = []
    for port in design.get("subscribers") or []:
        header, message = cpp_type(port["message_type"])
        headers.add(header)
        body.append(f"input<{message}>({quoted(port['name'])}, {qos_expression(port, True)});")
    for port in design.get("publishers") or []:
        header, message = cpp_type(port["message_type"])
        headers.add(header)
        body.append(f"output<{message}>({quoted(port['name'])}, {qos_expression(port, False)});")
    for port in design.get("servers") or []:
        header, service = cpp_type(port["message_type"])
        headers.add(header)
        body.append(f"server<{service}>({quoted(port['name'])});")

    processes = design.get("processes") or []
    triggers = {process["name"]: [] for process in processes}
    for process in processes:
        for outcome in process.get("outcomes") or []:
            if isinstance(outcome, dict) and outcome.get("to_trigger") in triggers:
                triggers[process["name"]].append(outcome["to_trigger"])
        for condition in process.get("trigger_conditions") or []:
            source = condition.get("on_trigger") if isinstance(condition, dict) else None
            if source in triggers and process["name"] not in triggers[source]:
                triggers[source].append(process["name"])
    for process in processes:
        name = process["name"]
        outputs = [o["to_output"] for o in process.get("outcomes") or [] if isinstance(o, dict) and "to_output" in o]
        body.append(
            f"process({quoted(name)}, {{{', '.join(quoted(o) for o in outputs)}}}, "
            f"{{{', '.join(quoted(t) for t in triggers[name])}}});"
        )
        for condition in process.get("trigger_conditions") or []:
            for rule in condition_rules(condition)[0]:
                expression = rule_expression(name, rule)
                if rule.kind == "timer":
                    body.append(f"on_timer({rule.rate}, {expression});")
                elif rule.kind == "input":
                    body.append(f"on_input({quoted(rule.source)}, {expression});")
                else:
                    body.append(f"on_start({expression});")

    node_name = snake(design["name"].removesuffix(".node"))
    lines = "\n".join(f"    {line}" for line in body)
    source = f"""namespace {namespace}
{{
class {class_name} : public autoware::system_dummy_modules::DummyNode
{{
public:
  explicit {class_name}(const rclcpp::NodeOptions & options) : DummyNode({quoted(node_name)}, options)
  {{
{lines}
  }}
}};
}}  // namespace {namespace}
RCLCPP_COMPONENTS_REGISTER_NODE({plugin})
"""
    return plugin, executable, source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-dir", required=True, type=Path, help="directory of the C++ sources to write")
    parser.add_argument("--cmake", required=True, type=Path, help="CMake list of sources, plugins and executables")
    parser.add_argument("designs", nargs="+", type=Path, help="node design files")
    args = parser.parse_args()

    files = {}
    plugins, executables, sources = [], [], []
    for path in sorted(args.designs):
        headers: set = set()
        plugin, executable, body = generate_class(yaml.safe_load(path.read_text()), path, headers)
        includes = "\n".join(f"#include <{header}>" for header in sorted(headers))
        source = args.source_dir / f"{executable}.cpp"
        files[source] = f"""// Generated by generate_dummy_nodes.py from {path.name}.

#include "autoware/system_dummy_modules/dummy_node.hpp"

#include <rclcpp_components/register_node_macro.hpp>

{includes}

using autoware::system_dummy_modules::port_qos;

{body}"""
        plugins.append(plugin)
        executables.append(executable)
        sources.append(str(source))

    def cmake_list(name: str, values: List[str]) -> str:
        return f"set({name}\n" + "".join(f'  "{value}"\n' for value in values) + ")\n"

    files[args.cmake] = (
        cmake_list("DUMMY_NODE_SOURCES", sources)
        + cmake_list("DUMMY_NODE_PLUGINS", plugins)
        + cmake_list("DUMMY_NODE_EXECUTABLES", executables)
    )
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
