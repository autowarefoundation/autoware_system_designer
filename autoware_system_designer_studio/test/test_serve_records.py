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

"""The record watcher reports a new latency record of a system and mode once it is complete."""

import asyncio

from autoware_system_designer_studio.serve import events, record_watch


def _hub():
    return events.EventHub(max_subscribers=4, heartbeat_s=60.0)


def _record(root, package, system, mode, stamp, text="{}"):
    directory = root / package / system / "latency" / mode
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}.json"
    path.write_text(text)
    return path


def test_first_scan_seeds_without_events(tmp_path):
    _record(tmp_path, "pkg", "Sys", "Runtime", "20260101-000000")
    watcher = record_watch.RecordWatcher(_hub(), tmp_path)
    assert watcher.scan() == []
    assert watcher.scan() == []


def test_new_record_is_reported_once_its_size_holds(tmp_path):
    hub = _hub()
    watcher = record_watch.RecordWatcher(hub, tmp_path)
    watcher.scan()
    path = _record(tmp_path, "pkg", "Sys", "Runtime", "20260101-000100", "{")
    assert watcher.scan() == []  # first sight
    path.write_text('{"schema": "x"}')
    assert watcher.scan() == []  # still growing
    published = watcher.scan()
    assert published == [{"system": "pkg:Sys", "mode": "Runtime", "record": str(path)}]
    assert watcher.scan() == []


def test_events_reach_subscribers_with_the_topic(tmp_path):
    async def run():
        hub = _hub()
        subscriber = hub.subscribe()
        watcher = record_watch.RecordWatcher(hub, tmp_path)
        watcher.scan()
        _record(tmp_path, "pkg", "Sys", "Sim", "20260101-000200")
        watcher.scan()
        watcher.scan()
        event = subscriber.queue.get_nowait()
        assert event.topic == record_watch.TOPIC_RECORDS_CHANGED
        assert event.data["system"] == "pkg:Sys" and event.data["mode"] == "Sim"

    asyncio.run(run())


def test_partial_files_and_other_suffixes_are_ignored(tmp_path):
    watcher = record_watch.RecordWatcher(_hub(), tmp_path)
    watcher.scan()
    directory = tmp_path / "pkg" / "Sys" / "latency" / "Runtime"
    directory.mkdir(parents=True)
    (directory / ".20260101-000300.json.part").write_text("{")
    (directory / "notes.txt").write_text("x")
    assert watcher.scan() == []
    assert watcher.scan() == []


def test_newest_record_by_name_wins(tmp_path):
    watcher = record_watch.RecordWatcher(_hub(), tmp_path)
    _record(tmp_path, "pkg", "Sys", "Runtime", "20260101-000500")
    watcher.scan()
    older = _record(tmp_path, "pkg", "Sys", "Runtime", "20260101-000400")
    assert watcher.scan() == [] and watcher.scan() == []
    newer = _record(tmp_path, "pkg", "Sys", "Runtime", "20260101-000600")
    watcher.scan()
    assert [e["record"] for e in watcher.scan()] == [str(newer)]
    assert older.exists()


def test_removed_directories_are_forgotten(tmp_path):
    watcher = record_watch.RecordWatcher(_hub(), tmp_path)
    path = _record(tmp_path, "pkg", "Sys", "Runtime", "20260101-000700")
    watcher.scan()
    path.unlink()
    watcher.scan()
    assert not watcher._known
