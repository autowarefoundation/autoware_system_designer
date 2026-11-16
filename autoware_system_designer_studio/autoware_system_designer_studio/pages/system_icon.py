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

"""Default icon of a system: a tile with its monogram, coloured by its id."""

from __future__ import annotations

import hashlib
import re
from xml.sax.saxutils import escape

MONOGRAM_LETTERS = 2
_WORD_START = re.compile(r"[A-Z][a-z0-9]*|[a-z0-9]+")

_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="hsl({hue}, 52%, 42%)"/>'
    '<path d="M16 5.5 25 10.75v10.5L16 26.5 7 21.25v-10.5z" fill="none" stroke="#fff" stroke-opacity="0.28"/>'
    '<text x="16" y="16" dy="0.36em" text-anchor="middle" fill="#fff" font-family="Helvetica, Arial, sans-serif"'
    ' font-size="{size}" font-weight="700">{monogram}</text>'
    "</svg>\n"
)


def monogram(name: str) -> str:
    """Initials of the first words of a CamelCase or separated name: ``PilotAutoX2Gen2`` gives ``PA``."""
    words = _WORD_START.findall(name)
    letters = "".join(word[0] for word in words[:MONOGRAM_LETTERS]).upper()
    return letters or "?"


def default_icon(system_id: str, name: str) -> str:
    """SVG text of the default icon; the hue is stable per system id."""
    hue = int.from_bytes(hashlib.sha256(system_id.encode()).digest()[:2], "big") % 360
    letters = monogram(name)
    return _TEMPLATE.format(hue=hue, size=14 if len(letters) > 1 else 17, monogram=escape(letters))
