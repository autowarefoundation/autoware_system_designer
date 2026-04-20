# Copyright 2023 M. Fatih Cırıt
# Modifications copyright 2026 TIER IV, Inc.
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

import pathlib

from jinja2 import Template
from rclpy.logging import get_logger

logger = get_logger("launch2json")

_TEMPLATES_DIR = pathlib.Path(__file__).parent / "templates"


def _get_all_entities(tree: dict):
    if tree.get("children"):
        entities = [tree["entity"]]
        for subtree in tree["children"]:
            entities.extend(_get_all_entities(subtree))
        return entities
    else:
        return [tree]


def get_component_kind(entity: dict):
    if entity["type"] == "IncludeLaunchDescription":
        return "folder"
    if entity["type"] == "GroupAction":
        return "folder"
    if entity["type"] == "ComposableNodeContainer":
        return "folder"
    if entity["type"] == "LoadComposableNodes":
        return "folder"
    if entity["type"] == "ComposableNode":
        return "node"
    if entity["type"] == "Node":
        return "node"
    if entity["type"] == "ExecuteProcess":
        return "node"
    return "card"


def get_component_id(entity: dict):
    if entity["type"] == "IncludeLaunchDescription":
        return f'{entity["type"]}_{entity["id"]}'
    if entity["type"] == "GroupAction":
        return f'{entity["type"]}_{entity["id"]}'
    if entity["type"] == "ComposableNodeContainer":
        return f'{entity["type"]}_{entity["id"]}'
    if entity["type"] == "LoadComposableNodes":
        return f'{entity["type"]}_{entity["id"]}'
    if entity["type"] == "ComposableNode":
        return f'{entity["type"]}_{entity["id"]}'
    if entity["type"] == "Node":
        return f'{entity["type"]}_{entity["id"]}'
    if entity["type"] == "ExecuteProcess":
        return f'{entity["type"]}_{entity["id"]}'
    return f'Unknown_{entity["type"]}_{entity["id"]}'


def get_component_style(entity: dict):
    if entity["type"] == "IncludeLaunchDescription":
        return "#LightSalmon"
    if entity["type"] == "GroupAction":
        return "#Pink"
    if entity["type"] == "ComposableNodeContainer":
        return "#LemonChiffon"
    if entity["type"] == "LoadComposableNodes":
        return "#LightGreen"
    if entity["type"] == "ComposableNode":
        return "#PaleTurquoise"
    if entity["type"] == "Node":
        return "#LightSkyBlue"
    if entity["type"] == "ExecuteProcess":
        return "#Wheat"
    if entity["type"] == "SetParameter":
        return "#Orange"
    if entity["type"] == "SetRemap":
        return "#Khaki"
    return "#Pink"


def create_entity_index_map(tree: dict):
    index_map = {}

    def update_index(tree):
        if tree.get("children"):
            index_map[str(tree["entity"])] = tree
            for subtree in tree["children"]:
                update_index(subtree)
        else:
            index_map[str(tree)] = tree

    update_index(tree)

    return index_map


def get_children(index_map: dict, entity: dict):
    index = index_map[str(entity)]
    if not index.get("children"):
        return []

    children = []
    for child in index["children"]:
        if child.get("children"):
            children.append(child["entity"])
        else:
            children.append(child)

    return children


def generate_plantuml(serializable_tree: dict):
    template_text = (_TEMPLATES_DIR / "launch_plantuml.jinja2").read_text()
    template = Template(template_text)

    entities = _get_all_entities(serializable_tree)
    for i, e in enumerate(entities):
        e["id"] = i

    def escape_plantuml_brackets(string):
        """Escapes the [ and ] characters in a string for use in PlantUML."""
        return string.replace("[", "\\[").replace("]", "\\]")

    def escape_plantuml_brackets_in_dict(entities):
        """Escapes the [ and ] characters in all strings in a dictionary."""
        for key, value in entities.items():
            if isinstance(value, str):
                entities[key] = escape_plantuml_brackets(value)
            elif isinstance(value, dict):
                escape_plantuml_brackets_in_dict(value)

    for entity in entities:
        escape_plantuml_brackets_in_dict(entity)
        list_keys_to_remove = []
        for key, value in entity.items():
            if "param" in key:
                list_keys_to_remove.append(key)
            elif "remap_rules" in key:
                list_keys_to_remove.append(key)
            elif "arguments" in key:
                list_keys_to_remove.append(key)
        for key in list_keys_to_remove:
            del entity[key]

    index_map = create_entity_index_map(serializable_tree)

    return template.render(
        {
            "entities": entities,
            "index_map": index_map,
            "get_component_kind": get_component_kind,
            "get_component_id": get_component_id,
            "get_component_style": get_component_style,
            "get_children": get_children,
        }
    )
