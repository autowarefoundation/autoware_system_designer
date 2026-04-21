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

from rclpy.logging import get_logger

logger = get_logger("launch2json")

_TEMPLATES_DIR = pathlib.Path(__file__).parent / "templates"


def _get_all_entities(tree: dict):
    if "entity" in tree:
        entities = [tree["entity"]]
        for subtree in tree.get("children", []):
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
        return "#Salmon"
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
        if "entity" in tree:
            index_map[str(tree["entity"])] = tree
            for subtree in tree.get("children", []):
                update_index(subtree)
        else:
            index_map[str(tree)] = tree

    update_index(tree)

    return index_map


def get_children(index_map: dict, entity: dict):
    index = index_map[str(entity)]
    if "entity" not in index:
        return []

    children = []
    for child in index.get("children", []):
        if "entity" in child:
            children.append(child["entity"])
        else:
            children.append(child)

    return children


def generate_launch_file(serializable_tree: dict):
    template_text = (_TEMPLATES_DIR / "launch_generated.jinja2").read_text()

    entities = _get_all_entities(serializable_tree)

    # sort entities by their types
    entities = sorted(entities, key=lambda e: get_component_kind(e))

    def format_param(value):
        # If value is a float, format without scientific notation
        if isinstance(value, float):
            formatted_value = "{:f}".format(value)
            # If there's a decimal point and trailing zeros
            if "." in formatted_value:
                # Remove all but one trailing zero and then remove trailing dots, if any
                formatted_value = formatted_value.rstrip("0")
                if formatted_value[-1] != "0":
                    formatted_value += "0"
                formatted_value = formatted_value.rstrip(".")
            value = formatted_value

        # For other types (assuming they can be converted to strings with str())
        value = str(value)

        # Replace parentheses with brackets
        value = value.replace("(", "[").replace(")", "]")

        return value

    from jinja2 import Environment

    env = Environment(loader=None)  # No loader since we're providing the template string directly
    env.filters["format_param"] = format_param

    template = env.from_string(template_text)

    return template.render({"entities": entities})
