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

"""Declared compute units of a resolved system and the component binding rule."""

from __future__ import annotations

from typing import List

from autoware_system_designer.common.exceptions import ValidationError
from autoware_system_designer.common.source_location import format_source, source_from_config
from autoware_system_designer.model.config import SystemConfig


def compute_unit_names(system_config: SystemConfig) -> List[str]:
    """Declared unit names in declaration order; empty when the system declares none."""
    return [unit.name for unit in system_config.compute_units or []]


def check_component_compute_units(system_config: SystemConfig) -> None:
    """Once a system declares compute units, every component binds to exactly one of them.

    Runs on the resolved config (base, variant and mode merged), so a variant or
    mode override cannot introduce a component on an undeclared unit.
    """
    declared = compute_unit_names(system_config)
    if not declared:
        return

    duplicates = sorted({name for name in declared if declared.count(name) > 1})
    if duplicates:
        src = source_from_config(system_config, "/compute_units")
        raise ValidationError(f"Duplicate compute unit declaration(s): {duplicates}{format_source(src)}")

    for idx, component in enumerate(system_config.components or []):
        if not isinstance(component, dict):
            continue
        unit = component.get("compute_unit")
        if unit in declared:
            continue
        src = source_from_config(system_config, f"/components/{idx}/compute_unit")
        component_name = component.get("name")
        if unit is None:
            raise ValidationError(
                f"Component '{component_name}' has no compute_unit; declared compute units: {declared}{format_source(src)}"
            )
        raise ValidationError(
            f"Component '{component_name}' references undeclared compute unit '{unit}'; "
            f"declared compute units: {declared}{format_source(src)}"
        )
