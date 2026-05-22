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

"""Actor configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ActorConfig:
    """Per-actor runtime configuration.

    Mirrors play_launch ``ActorConfig`` in ``member_actor/state.rs``.
    """

    respawn_enabled: bool = False
    respawn_delay: float = 1.0
    max_respawn_attempts: Optional[int] = None  # None = infinite
    output_dir: Path = field(default_factory=lambda: Path("/tmp/autoware_system_designer_logs"))
    graceful_shutdown_timeout: float = 5.0  # seconds — SIGTERM grace before SIGKILL
