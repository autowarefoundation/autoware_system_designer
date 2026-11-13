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

"""Subscription dispatch of the ros worker's executor on the installed ROS distribution."""

from __future__ import annotations

import os
import time

import pytest

rclpy = pytest.importorskip("rclpy")

from autoware_system_designer_runtime._impl.ros2.launchers.container import make_executor  # noqa: E402


@pytest.mark.timing
def test_executor_passes_message_info_of_a_latched_sample_to_two_argument_callbacks(ros_domain_id):
    from builtin_interfaces.msg import Time
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from rclpy.signals import SignalHandlerOptions

    context = rclpy.context.Context()
    rclpy.init(context=context, domain_id=ros_domain_id, signal_handler_options=SignalHandlerOptions.NO)
    try:
        node = rclpy.create_node("asd_test_executor", context=context)
        executor = make_executor(context)
        executor.add_node(node)
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        topic = f"/asd_test_executor_{os.getpid()}"
        node.create_publisher(Time, topic, qos).publish(Time(sec=1))
        published_before = time.time_ns()
        time.sleep(0.5)

        with_info, message_only = [], []
        node.create_subscription(Time, topic, lambda msg, info: with_info.append((msg, info)), qos)
        node.create_subscription(Time, topic, lambda msg: message_only.append(msg), qos)
        deadline = time.monotonic() + 5.0
        while (not with_info or not message_only) and time.monotonic() < deadline:
            executor.spin_once(timeout_sec=0.1)

        assert [m.sec for m in message_only] == [1]
        assert len(with_info) == 1
        msg, info = with_info[0]
        assert msg.sec == 1
        # The latched sample keeps its original publication time.
        assert 0 < info["source_timestamp"] <= published_before
        executor.shutdown()
        node.destroy_node()
    finally:
        rclpy.shutdown(context=context)
