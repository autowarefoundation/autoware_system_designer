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

"""Recorded outputs are paired with their publishes and compared with a reference by rule."""

from autoware_system_designer_runtime._impl.replay.config import CompareRule, ReplayConfig
from autoware_system_designer_runtime._impl.replay.recorder import OutputRecorder
from autoware_system_designer_runtime._impl.replay.verdict import (
    DIFFER,
    IGNORED,
    MATCH,
    compare_outputs,
    compare_topic,
    digest,
    group_by_topic,
)


class Sink:
    def __init__(self):
        self.written = []

    def write(self, topic, data, stamp_ns):
        self.written.append((topic, data, stamp_ns))


def test_recorder_writes_in_publish_order_whatever_the_arrival_order():
    sink = Sink()
    recorder = OutputRecorder({"/a": "t", "/b": "t"}, sink)
    recorder.start()

    recorder.published("/a", 10)
    recorder.published("/b", 10)
    recorder.published("/a", 10)
    recorder.heard("/b", b"b1")
    recorder.heard("/a", b"a1")
    assert recorder.missing() == "/a"
    recorder.flush(10)
    assert sink.written == []

    recorder.heard("/a", b"a2")
    assert recorder.missing() is None
    recorder.flush(10)

    assert sink.written == [("/a", b"a1", 10), ("/b", b"b1", 10), ("/a", b"a2", 10)]


def test_recorder_drops_what_it_heard_before_the_start_and_other_topics():
    sink = Sink()
    recorder = OutputRecorder({"/a": "t"}, sink)

    recorder.heard("/a", b"early")
    recorder.published("/a", 1)
    recorder.start()
    recorder.heard("/other", b"x")
    recorder.published("/other", 2)
    recorder.flush(2)

    assert sink.written == [] and recorder.missing() is None


def test_recorder_writes_unannounced_messages_at_the_flush_time_and_forgives_lost_ones():
    sink = Sink()
    recorder = OutputRecorder({"/a": "t", "/b": "t"}, sink)
    recorder.start()

    recorder.heard("/a", b"stray")
    recorder.flush(5)
    assert sink.written == [("/a", b"stray", 5)] and recorder.unannounced == 1

    recorder.published("/a", 6)
    recorder.published("/b", 6)
    recorder.heard("/a", b"a")
    recorder.forgive("/b")
    recorder.flush(6)
    assert sink.written[-1] == ("/a", b"a", 6) and recorder.lost == 1 and recorder.missing() is None


class Vector:
    def __init__(self, x, y):
        self.x, self.y = x, y

    def get_fields_and_field_types(self):
        return {"x": "double", "y": "double"}


class Sample:
    def __init__(self, stamp, points, label="a"):
        self.stamp, self.points, self.label = stamp, points, label

    def get_fields_and_field_types(self):
        return {"stamp": "int64", "points": "sequence<Vector>", "label": "string"}


def encode(sample: Sample) -> bytes:
    return repr((sample.stamp, [(p.x, p.y) for p in sample.points], sample.label)).encode()


def decode(_msg_type: str, data: bytes) -> Sample:
    stamp, points, label = eval(data.decode())  # noqa: S307 - test data written by encode
    return Sample(stamp, [Vector(x, y) for x, y in points], label)


def records(*samples):
    return [(index * 100, encode(sample)) for index, sample in enumerate(samples)]


def test_exact_rule_reports_the_first_differing_message():
    reference = records(Sample(1, [Vector(1.0, 2.0)]), Sample(2, [Vector(1.0, 2.0)]))
    recorded = records(Sample(1, [Vector(1.0, 2.0)]), Sample(2, [Vector(1.0, 2.5)]))

    assert compare_topic("/t", "T", reference, reference, CompareRule()).status == MATCH
    verdict = compare_topic("/t", "T", reference, recorded, CompareRule())
    assert verdict.status == DIFFER and verdict.difference["index"] == 1


def test_exact_rule_and_digest_follow_the_content_of_differing_bytes():
    reference = records(Sample(1, [Vector(1.0, 2.0)]))
    padded = [(stamp, data + b" ") for stamp, data in reference]
    changed = records(Sample(1, [Vector(1.0, 2.5)]))

    assert compare_topic("/t", "T", reference, padded, CompareRule(), decode).status == MATCH
    assert compare_topic("/t", "T", reference, padded, CompareRule()).status == DIFFER
    verdict = compare_topic("/t", "T", reference, changed, CompareRule(), decode)
    assert verdict.status == DIFFER and "points.y" in verdict.difference["detail"]

    types = {"/t": "T"}
    assert digest({"/t": reference}, types, decode) == digest({"/t": padded}, types, decode)
    assert digest({"/t": reference}, types, decode) != digest({"/t": changed}, types, decode)
    assert digest({"/t": reference}) != digest({"/t": padded})


def test_count_and_driver_time_differences_are_reported():
    reference = records(Sample(1, []), Sample(2, []))

    shorter = compare_topic("/t", "T", reference, reference[:1], CompareRule())
    assert shorter.status == DIFFER and "1 messages, reference 2" in shorter.difference["detail"]

    shifted = [(reference[0][0], reference[0][1]), (reference[1][0] + 1, reference[1][1])]
    late = compare_topic("/t", "T", reference, shifted, CompareRule(rule="fields"), decode)
    assert late.status == DIFFER and "driver time" in late.difference["detail"]


def test_fields_rule_applies_tolerance_and_ignored_fields():
    reference = records(Sample(1, [Vector(1.0, 2.0), Vector(3.0, 4.0)]))
    recorded = records(Sample(9, [Vector(1.0, 2.0), Vector(3.0, 4.0005)]))

    strict = compare_topic("/t", "T", reference, recorded, CompareRule(rule="fields"), decode)
    assert strict.status == DIFFER and strict.difference["detail"].startswith("stamp")

    loose = CompareRule(rule="fields", abs_tolerance=1e-3, ignore_fields=["stamp"])
    assert compare_topic("/t", "T", reference, recorded, loose, decode).status == MATCH

    tight = CompareRule(rule="fields", abs_tolerance=1e-6, ignore_fields=["stamp"])
    verdict = compare_topic("/t", "T", reference, recorded, tight, decode)
    assert verdict.status == DIFFER and "points.y" in verdict.difference["detail"]
    assert "element 1" in verdict.difference["detail"]


def test_fields_rule_reports_length_and_text_differences():
    reference = records(Sample(1, [Vector(1.0, 2.0)], "a"))

    longer = compare_topic(
        "/t", "T", reference, records(Sample(1, [Vector(1.0, 2.0)] * 2, "a")), CompareRule(rule="fields"), decode
    )
    assert "2 elements, reference 1" in longer.difference["detail"]
    renamed = compare_topic(
        "/t", "T", reference, records(Sample(1, [Vector(1.0, 2.0)], "b")), CompareRule(rule="fields"), decode
    )
    assert renamed.difference["detail"].startswith("label")


class UUID:
    __module__ = "unique_identifier_msgs.msg._uuid"

    def __init__(self, value):
        self.uuid = [value] * 16

    def get_fields_and_field_types(self):
        return {"uuid": "uint8[16]"}


class Track:
    def __init__(self, ident):
        self.id = UUID(ident)

    def get_fields_and_field_types(self):
        return {"id": "unique_identifier_msgs/UUID"}


def test_uuid_order_compares_identifiers_by_first_appearance():
    def tracks(*idents):
        return [(index, bytes([ident])) for index, ident in enumerate(idents)]

    def decode_track(_msg_type, data):
        return Track(data[0])

    rule = CompareRule(rule="fields", uuid_order=True)
    # 7, 7, 9 and 4, 4, 5 are both "first, first, second".
    assert compare_topic("/t", "T", tracks(7, 7, 9), tracks(4, 4, 5), rule, decode_track).status == MATCH
    verdict = compare_topic("/t", "T", tracks(7, 7, 9), tracks(4, 5, 5), rule, decode_track)
    assert verdict.status == DIFFER and verdict.difference["index"] == 1
    assert compare_topic("/t", "T", tracks(7), tracks(4), CompareRule(rule="fields"), decode_track).status == DIFFER


def test_outputs_are_compared_topic_by_topic_with_ignore_and_missing_topics():
    config = ReplayConfig.from_mapping({"compare": {"topics": {"/diag": "ignore"}}})
    reference = {"/out": records(Sample(1, [])), "/diag": records(Sample(1, []))}
    recorded = {"/out": records(Sample(1, [])), "/diag": records(Sample(7, []))}

    verdict = compare_outputs(reference, recorded, {"/out": "T", "/diag": "T", "/silent": "T"}, config)
    assert verdict.passed
    assert {t.topic: t.status for t in verdict.topics} == {"/diag": IGNORED, "/out": MATCH, "/silent": MATCH}

    verdict = compare_outputs(reference, {"/diag": recorded["/diag"]}, {"/out": "T"}, config)
    assert not verdict.passed


def test_grouping_leaves_out_the_warmup_and_the_digest_follows_the_content():
    messages = [(100, "/a", b"1"), (200, "/b", b"2"), (300, "/a", b"3")]

    assert group_by_topic(messages, since_ns=200) == {"/b": [(200, b"2")], "/a": [(300, b"3")]}
    assert digest(group_by_topic(messages)) == digest(group_by_topic(list(messages)))
    assert digest(group_by_topic(messages)) != digest(group_by_topic(messages[:2]))
