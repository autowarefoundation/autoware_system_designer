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

from autoware_system_designer.builder.parameters import parameter_resolver
from autoware_system_designer.builder.parameters.parameter_resolver import ParameterResolver


def _resolver() -> ParameterResolver:
    resolver = ParameterResolver([{"name": "vehicle_model", "value": "car"}], {})
    resolver.declare_arguments(["individual_params_path"])
    return resolver


def _capture_warnings(monkeypatch):
    warnings = []
    monkeypatch.setattr(parameter_resolver.logger, "warning", lambda message, *args: warnings.append(message))
    return warnings


def test_variable_resolves_at_build_time():
    assert _resolver().resolve_string("$(var vehicle_model)_description") == "car_description"


def test_argument_stays_for_launch_time_without_warning(monkeypatch):
    warnings = _capture_warnings(monkeypatch)
    resolver = _resolver()
    result = resolver.resolve_string("$(var individual_params_path)/robot_description.param.yaml")
    assert result == "$(var individual_params_path)/robot_description.param.yaml"
    assert resolver.undefined_variables == {}
    assert warnings == []


def test_undeclared_name_is_recorded_and_warned_once(monkeypatch):
    warnings = _capture_warnings(monkeypatch)
    resolver = _resolver()
    resolver.resolve_string("$(var vehical_model)")
    resolver.resolve_string("$(var vehical_model)")
    assert list(resolver.undefined_variables) == ["vehical_model"]
    assert len(warnings) == 1 and "$(var vehical_model)" in warnings[0]


def test_copies_share_arguments_and_undefined_record():
    resolver = _resolver()
    child = resolver.copy()
    assert child.resolve_string("$(var individual_params_path)") == "$(var individual_params_path)"
    child.resolve_string("$(var unknown)")
    assert "unknown" in resolver.undefined_variables
