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

import pytest

from autoware_system_designer.builder.parameters.parameter_resolver import (
    ParameterResolver,
    evaluate_safe_expression,
)


def _resolver() -> ParameterResolver:
    return ParameterResolver([{"name": "scale", "value": 4}], {})


def test_arithmetic_and_math_functions():
    resolver = _resolver()
    assert resolver.resolve_string("$(eval 1 + 2 * 3)") == "7"
    assert resolver.resolve_string("$(eval round(sin(pi / 2), 3))") == "1.0"
    assert resolver.resolve_string("$(eval math.floor(2.7))") == "2"
    assert resolver.resolve_string("$(eval max(1, 5) - min(2, 3))") == "3"
    assert resolver.resolve_string("$(eval 2 ** 3 if 1 < 2 else 0)") == "8"
    assert resolver.resolve_string("$(eval [1, 2])") == "[1, 2]"


def test_variables_resolve_inside_eval():
    assert _resolver().resolve_string("$(eval $(var scale) * 2.5)") == "10.0"


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "open('x')",
        "'abc'.upper()",
        "math.__name__",
        "(lambda: 1)()",
        "[1][0]",
        "str(1).__class__",
        "max(*[1, 2])",
        "0 if 1 > 2 else exit()",
    ],
)
def test_unsupported_constructs_are_rejected(expression):
    with pytest.raises(ValueError):
        evaluate_safe_expression(expression)
    text = f"$(eval {expression})"
    assert _resolver().resolve_string(text) == text


@pytest.mark.parametrize(
    "expression",
    [
        "9 ** 9 ** 9 ** 9",
        "2 ** 100000",
        "pow(2, 10 ** 10)",
        "'x' * 10 ** 8",
        "[1] * 10 ** 8",
        "10 ** 1000 * 10 ** 1000",
        "math.factorial(10 ** 9)",
        "int('9' * 4096) + 1",
    ],
)
def test_unbounded_results_are_rejected(expression):
    with pytest.raises(ValueError):
        evaluate_safe_expression(expression)


def test_bounded_results_are_computed():
    assert evaluate_safe_expression("2 ** 64") == 2**64
    assert evaluate_safe_expression("'ab' * 3") == "ababab"
    assert evaluate_safe_expression("math.gcd(12, 18)") == 6


def test_syntax_errors_leave_text_unchanged():
    assert _resolver().resolve_string("$(eval 1 +)") == "$(eval 1 +)"
