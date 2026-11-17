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

from autoware_system_designer_runtime.special_topics import (
    SPECIAL_TOPICS_FILE,
    SpecialTopics,
    SpecialTopicsError,
    special_topics,
)


def test_the_package_file_gives_every_role():
    topics = special_topics()
    assert SPECIAL_TOPICS_FILE.is_file()
    assert topics.infrastructure == {"/rosout", "/parameter_events"}
    assert topics.process == {"/rosout", "/parameter_events", "/clock"}
    assert topics.companions == {"/tf": ("/tf_static",)}
    assert topics.companion_of("/tf_static") == "/tf"
    assert topics.companion_of("/tf") is None
    assert topics.ambient_inputs == ("/tf", "/tf_static")
    assert topics.nested_callbacks == {"/tf", "/tf_static"}
    assert topics.is_handshake("/camera/image/_supported_types")
    assert not topics.is_handshake("/camera/image")


def test_roles_come_from_the_mapping_alone():
    topics = SpecialTopics.from_mapping(
        {"infrastructure": ["/log"], "clock": "/time", "companions": {"/pose": ["/pose_static"]}}
    )
    assert topics.process == {"/log", "/time"}
    assert topics.companion_of("/pose_static") == "/pose"
    assert topics.ambient_inputs == ()
    assert not topics.is_handshake("/any/_supported_types")
    assert SpecialTopics.from_mapping(topics.as_dict()) == topics
    assert SpecialTopics.from_mapping(None) == SpecialTopics()


@pytest.mark.parametrize(
    "payload",
    [["/rosout"], {"infra": ["/rosout"]}, {"clock": [1]}, {"companions": ["/tf"]}, {"companions": {"/tf": 3}}],
)
def test_a_malformed_file_is_refused(payload):
    with pytest.raises(SpecialTopicsError):
        SpecialTopics.from_mapping(payload)


def test_an_unreadable_file_is_refused(tmp_path):
    with pytest.raises(SpecialTopicsError):
        SpecialTopics.load(tmp_path / "missing.yaml")
