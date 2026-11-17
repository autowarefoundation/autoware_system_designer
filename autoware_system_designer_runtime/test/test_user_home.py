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

from autoware_system_designer_runtime import user_home
from autoware_system_designer_runtime._impl.replay import middleware
from autoware_system_designer_runtime.special_topics import SpecialTopicsError, special_topics


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    monkeypatch.setenv(user_home.HOME_ENV, str(path))
    return path


def _override(home, name, text):
    directory = home / user_home.CONFIG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text)
    return directory / name


def test_home_defaults_under_the_user_directory(monkeypatch):
    monkeypatch.delenv(user_home.HOME_ENV)
    assert user_home.home() == user_home.DEFAULT_HOME.expanduser()


def test_without_a_user_file_the_package_file_alone_is_read(home):
    payload, files = user_home.read_config("special_topics.yaml")
    assert files == [user_home.PACKAGE_CONFIG_DIR / "special_topics.yaml"]
    assert payload["clock"] == ["/clock"]
    assert user_home.user_overrides() == []


def test_a_user_file_replaces_the_top_level_keys_it_sets(home):
    path = _override(home, "special_topics.yaml", "ambient_inputs: [/tf]\n")
    assert user_home.user_overrides() == [path]

    topics = special_topics()
    assert topics.ambient_inputs == ("/tf",)
    assert topics.clock == {"/clock"}


def test_a_user_file_overrides_the_replay_middleware(home):
    _override(home, "replay_middleware.yaml", "isolation_environment: {}\n")
    settings = middleware.replay_middleware()
    assert settings.isolation_environment == {}
    assert settings.node_parameters == {"use_sim_time": True}


def test_an_invalid_user_file_is_named_in_the_error(home):
    path = _override(home, "special_topics.yaml", "unknown_role: [/x]\n")
    with pytest.raises(SpecialTopicsError, match=str(path)):
        special_topics()


def test_a_user_file_that_is_no_mapping_is_named_in_the_error(home):
    path = _override(home, "special_topics.yaml", "- /x\n")
    with pytest.raises(SpecialTopicsError, match=f"{path}: expected a mapping"):
        special_topics()


def test_a_changed_user_file_is_read_again(home):
    assert special_topics().ambient_inputs == ("/tf", "/tf_static")
    path = _override(home, "special_topics.yaml", "ambient_inputs: [/tf]\n")
    assert special_topics().ambient_inputs == ("/tf",)
    assert special_topics() is special_topics()
    path.unlink()
    assert special_topics().ambient_inputs == ("/tf", "/tf_static")


def test_a_user_file_sets_driver_defaults(home):
    from autoware_system_designer_runtime._impl.replay.config import ConfigError, ReplayConfig, driver_settings

    defaults = {setting["name"]: setting["default"] for setting in driver_settings()}
    assert defaults["callback_timeout"] == 30.0
    path = _override(home, "replay_defaults.yaml", "driver: {callback_timeout: 5}\n")
    assert ReplayConfig().driver == {**defaults, "callback_timeout": 5.0}
    path.unlink()
    assert ReplayConfig.from_mapping({"driver": {"callback_timeout": 5, "miss_limit": 4}}).driver == {
        **defaults,
        "callback_timeout": 5.0,
        "miss_limit": 4,
    }
    for bad in ({"unknown": 1}, {"miss_limit": 0}, {"miss_limit": 1.5}, {"gate_hold": -1}):
        with pytest.raises(ConfigError):
            ReplayConfig.from_mapping({"driver": bad})
