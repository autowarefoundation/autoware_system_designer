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

"""Custom exceptions for the Autoware System Designer system.

A failure is stated once: the raising site owns the message, enclosing
layers attach context frames instead of re-wrapping, and the process
boundary renders message, frames, and hints as one block.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from autoware_system_designer.common.source_location import SourceLocation


@dataclass
class ContextFrame:
    """One layer of work the error passed through on its way out."""

    activity: str
    source: Optional["SourceLocation"] = None


class SystemDesignerError(Exception):
    """Base exception carrying the leaf message once, plus context frames and hints."""

    def __init__(self, message: str = "", source: Optional["SourceLocation"] = None):
        super().__init__(message)
        self.message = str(message)
        self.source = source
        self.context: List[ContextFrame] = []
        self.hints: List[str] = []

    def add_context(self, activity: str, source: Optional["SourceLocation"] = None) -> "SystemDesignerError":
        self.context.append(ContextFrame(activity, source))
        return self

    def add_hint(self, hint: str) -> "SystemDesignerError":
        if hint and hint not in self.hints:
            self.hints.append(hint)
        return self


class NodeConfigurationError(SystemDesignerError):
    """Exception raised for node configuration errors."""

    pass


class ModuleConfigurationError(SystemDesignerError):
    """Exception raised for module configuration errors."""

    pass


class ParameterConfigurationError(SystemDesignerError):
    """Exception raised for parameter configuration errors."""

    pass


class DeploymentError(SystemDesignerError):
    """Exception raised for deployment errors."""

    pass


class ValidationError(SystemDesignerError):
    """Exception raised for validation errors."""

    pass


class FormatVersionError(ValidationError):
    """Exception raised when a design file's format version is incompatible."""

    pass


def annotate_error(
    exc: Exception,
    activity: str,
    source: Optional["SourceLocation"] = None,
    wrap=ValidationError,
) -> SystemDesignerError:
    """A SystemDesignerError gains a context frame; a foreign exception is wrapped once."""
    if isinstance(exc, SystemDesignerError):
        return exc.add_context(activity, source)
    return wrap(str(exc), source=source).add_context(activity, source)


@contextmanager
def error_context(activity: str, source: Optional["SourceLocation"] = None, wrap=ValidationError):
    """Attach one context frame to any error escaping the block."""
    try:
        yield
    except Exception as exc:
        annotated = annotate_error(exc, activity, source, wrap)
        if annotated is exc:
            raise
        raise annotated from exc


def render_error(exc: SystemDesignerError) -> str:
    """One consolidated block: message and source, innermost-out context, hints."""
    from autoware_system_designer.common.source_location import format_source

    lines = [f"{exc.message}{format_source(exc.source) if exc.source else ''}"]
    if exc.context:
        lines.append("while:")
        for frame in exc.context:
            suffix = format_source(frame.source) if frame.source else ""
            lines.append(f"  - {frame.activity}{suffix}")
    lines.extend(exc.hints)
    return "\n".join(lines)
