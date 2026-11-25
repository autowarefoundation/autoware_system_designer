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

from unittest import mock

from autoware_system_designer.builder.parameters import parameter_resolver
from autoware_system_designer.builder.parameters.parameter_resolver import ParameterResolver


def test_command_substitution_warns_once_per_use():
    resolver = ParameterResolver([], {})
    value = "$(command 'xacro model.xacro')"

    with mock.patch.object(parameter_resolver.logger, "warning") as warning:
        assert resolver.resolve_string(value) == value
        resolver.copy().resolve_string(value)

    assert warning.call_count == 1
    assert value in warning.call_args.args[0]
