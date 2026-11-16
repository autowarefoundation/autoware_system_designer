// Copyright 2026 TIER IV, inc.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Replay side of the interposer: a process armed by ASD_REPLAY_SOCKET runs its
// subscription and timer callbacks one at a time, each on a grant of the replay
// driver, and reads the ROS time the driver sets. The hooks of tracer.c call in
// here; without the socket every call is a no-op.

#ifndef AUTOWARE_SYSTEM_DESIGNER_TRACER__REPLAY_H_
#define AUTOWARE_SYSTEM_DESIGNER_TRACER__REPLAY_H_

#include <stdbool.h>
#include <stdint.h>

#include <rcl/node.h>
#include <rcl/time.h>
#include <rcl/wait.h>
#include <rmw/types.h>

typedef rcl_ret_t (*asd_wait_fn)(rcl_wait_set_t *, int64_t);

// The process is linked to a replay driver; connects on the first call.
bool asd_replay_armed(void);
void asd_replay_at_fork_child(void);

// ---- endpoints ------------------------------------------------------------------

// Node the calling thread constructs; timers created outside a callback belong to it.
void asd_replay_node(const rcl_node_t * node);
// `kind` is 'p' or 's'.
void asd_replay_endpoint(
  char kind, const void * handle, const rcl_node_t * node, const char * topic, const rmw_qos_profile_t * qos);
void asd_replay_timer(const void * timer, rcl_clock_t * clock, int64_t period_ns, bool swapped);
void asd_replay_release(const void * handle);

// ---- callbacks ------------------------------------------------------------------

// rcl_wait that hands the executor one granted subscription or timer at a time.
rcl_ret_t asd_replay_wait(asd_wait_fn real, rcl_wait_set_t * wait_set, int64_t timeout);
// Around one executor callback of rclcpp; the outermost exit ends the grant of its thread.
void asd_replay_callback_enter(void);
void asd_replay_callback_exit(void);
void asd_replay_publish(const void * publisher);
void asd_replay_take(const void * subscription);
// A take that found no message while one is in flight: sleeps and returns true to try again.
bool asd_replay_take_retry(const void * subscription, unsigned attempt);
// After a timer call, a cancel or a reset: reports the timer's next call time.
void asd_replay_timer_called(const void * timer);
void asd_replay_timer_changed(const void * timer);

// ---- time -----------------------------------------------------------------------

// Clock a new timer runs on: the process's driver clock for a steady or system clock.
rcl_clock_t * asd_replay_timer_clock(rcl_clock_t * clock, bool * swapped);
void asd_replay_clock_enabled(rcl_clock_t * clock);
void asd_replay_clock_gone(rcl_clock_t * clock);
// A ROS time override from the process itself (its /clock subscription) is dropped.
bool asd_replay_drops_time_override(void);
// Held across every change of a clock's jump callbacks, which a time set walks.
void asd_replay_clock_lock(void);
void asd_replay_clock_unlock(void);

#endif  // AUTOWARE_SYSTEM_DESIGNER_TRACER__REPLAY_H_
