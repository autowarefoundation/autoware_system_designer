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

"""Custom exceptions for the Autoware System Designer system."""


class SystemDesignerError(Exception):
    """Base exception for system-designer related errors."""
    pass


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
