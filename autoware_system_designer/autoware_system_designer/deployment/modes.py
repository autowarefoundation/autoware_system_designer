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

from __future__ import annotations

import copy
import logging
from typing import List, Tuple

from ..builder.resolution.variant_resolver import SystemVariantResolver
from ..file_io.source_location import format_source, source_from_config
from ..models.config import SystemConfig

logger = logging.getLogger(__name__)


def apply_mode_configuration(base_system_config: SystemConfig, mode_name: str) -> SystemConfig:
    """Create a copy of base system and apply mode-specific overrides/removals."""

    modified_config = copy.deepcopy(base_system_config)

    # Filter out components with explicit 'mode' fields from base (deprecated old format)
    if modified_config.components:
        filtered_components = []
        for comp in modified_config.components:
            if "mode" in comp:
                logger.debug(
                    "Filtering out component '%s' with deprecated 'mode' field from base",
                    comp.get("name"),
                )
            else:
                filtered_components.append(comp)
        modified_config.components = filtered_components

    if mode_name == "default" or not base_system_config.mode_configs:
        return modified_config

    mode_config = base_system_config.mode_configs.get(mode_name)
    if not mode_config:
        src = source_from_config(base_system_config, "/modes")
        logger.warning(
            "Mode '%s' not found in mode_configs, using base configuration%s",
            mode_name,
            format_source(src),
        )
        return modified_config

    logger.info("Applying mode configuration for mode '%s'", mode_name)

    resolver = SystemVariantResolver()
    resolver.resolve(
        modified_config,
        {
            "override": mode_config.get("override", {}),
            "remove": mode_config.get("remove", {}),
        },
    )

    return modified_config


def select_modes(system_config: SystemConfig) -> Tuple[List[str], str]:
    """Return (mode_names, default_mode) for a SystemConfig."""

    modes_config = system_config.modes or []

    if modes_config:
        mode_names = [m.get("name") for m in modes_config]
        default_mode = next(
            (m.get("name") for m in modes_config if m.get("default")),
            mode_names[0],
        )
        return mode_names, default_mode

    return ["default"], "default"
