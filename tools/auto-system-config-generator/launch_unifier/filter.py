# Copyright 2023 M. Fatih Cırıt
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

import launch
import launch_ros
from rclpy.logging import get_logger

logger = get_logger("launch2json")

container_classes = (
    launch.actions.GroupAction,
    launch.actions.IncludeLaunchDescription,
    launch.actions.OpaqueFunction,
    launch.LaunchDescription,
)

ignore_container_classes = (
    launch.actions.OpaqueFunction,
    launch.LaunchDescription,
)

ignore_classes = (
    launch_ros.actions.PushRosNamespace,
    # launch_ros.actions.SetParameter,
    # launch_ros.actions.SetRemap,
    launch.actions.DeclareLaunchArgument,
    launch.actions.PopEnvironment,
    launch.actions.PopEnvironment,
    launch.actions.PopLaunchConfigurations,
    launch.actions.PushEnvironment,
    launch.actions.PushLaunchConfigurations,
    launch.actions.SetLaunchConfiguration,
    launch.actions.SetLaunchConfiguration,
)


SubtreeType = dict | list | launch.LaunchDescriptionEntity | launch_ros.descriptions.ComposableNode


def _remove_invalid_nodes(tree: SubtreeType):
    if isinstance(tree, dict):
        tree["children"] = _remove_invalid_nodes(tree["children"])
        return tree
    elif isinstance(tree, list):
        return [filtered for e in tree if (filtered := _remove_invalid_nodes(e))]
    elif isinstance(tree, launch.LaunchDescriptionEntity):
        if isinstance(tree, ignore_classes):
            return None
        # Container but "no children" or "failed to parse"
        if isinstance(tree, container_classes):
            return None
        return tree
    elif isinstance(tree, launch_ros.descriptions.ComposableNode):
        return tree


def _remove_unnecessary_containers(
    tree: SubtreeType,
):
    if isinstance(tree, dict):
        tree["children"] = _remove_unnecessary_containers(tree["children"])

        extend_items = []
        remove_items = []
        for child in tree["children"]:
            if not isinstance(child, dict):
                continue

            if isinstance(child["entity"], ignore_container_classes):
                if isinstance(child, dict):
                    extend_items.append(child["children"])
                    remove_items.append(child)

        # Execute extend/remove after the for-loop in order to avoid index collapse
        for extend_item in extend_items:
            tree["children"].extend(extend_item)
        for remove_item in remove_items:
            tree["children"].remove(remove_item)

        return tree
    elif isinstance(tree, list):
        return [filtered for e in tree if (filtered := _remove_unnecessary_containers(e))]
    elif isinstance(tree, launch.LaunchDescriptionEntity):
        return tree
    elif isinstance(tree, launch_ros.descriptions.ComposableNode):
        return tree


def _remove_empty_containers(tree: SubtreeType):
    if isinstance(tree, dict):
        tree["children"] = _remove_empty_containers(tree["children"])

        if isinstance(tree["entity"], container_classes):
            if tree["children"] == []:
                return None

        return tree
    elif isinstance(tree, list):
        return [filtered for e in tree if (filtered := _remove_empty_containers(e))]
    elif isinstance(tree, launch.LaunchDescriptionEntity):
        return tree
    elif isinstance(tree, launch_ros.descriptions.ComposableNode):
        return tree


def filter_entity_tree(tree: dict):
    tree = _remove_invalid_nodes(tree.copy())
    tree = _remove_unnecessary_containers(tree.copy())
    tree = _remove_empty_containers(tree.copy())
    # tree = remove_one_member_group(tree.copy()) # TODO
    return tree
