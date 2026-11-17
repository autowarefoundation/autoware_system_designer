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

// ROS time of a gated process: every ROS clock holds the time the driver set, and each gated
// timer's next call time is reported whenever it changes.

#include <stdlib.h>

#include <rcl/allocator.h>
#include <rcl/timer.h>

#include "replay_state.h"

static __thread bool tls_setting_time = false;

// ---- timers ------------------------------------------------------------------------

int64_t rp_timer_next(const entry_t * e)
{
  const rcl_timer_t * timer = (const rcl_timer_t *)e->handle;
  bool canceled = false;
  if (rcl_timer_is_canceled(timer, &canceled) != RCL_RET_OK || canceled) {
    return -1;
  }
  int64_t until = 0;
  rcl_time_point_value_t now = 0;
  if (
    rcl_timer_get_time_until_next_call(timer, &until) != RCL_RET_OK ||
    rcl_clock_get_now(e->clock, &now) != RCL_RET_OK)
  {
    return -1;
  }
  return now + until;
}

void rp_report_next(entry_t * e)
{
  if (!e->gated) {
    return;
  }
  int64_t next = rp_timer_next(e);
  if (next != e->next_ns) {
    e->next_ns = next;
    rp_send("next\t%u\t%lld", e->id, (long long)next);
  }
}

void asd_replay_timer_changed(const void * timer)
{
  pthread_mutex_lock(&asd_r_lock);
  entry_t * e = rp_find(timer);
  if (e != NULL) {
    rp_report_next(e);
  }
  pthread_mutex_unlock(&asd_r_lock);
}

// ---- time --------------------------------------------------------------------------

static void set_clock(rcl_clock_t * clock, int64_t ns)
{
  tls_setting_time = true;
  rcl_ret_t ret = rcl_set_ros_time_override(clock, ns);
  tls_setting_time = false;
  (void)ret;
}

void rp_on_time(unsigned long long seq, long long ns)
{
  pthread_mutex_lock(&asd_r_clock_lock);
  asd_r.time_ns = ns;
  asd_r.time_set = true;
  for (uint32_t i = 0; i < asd_r.clock_count; ++i) {
    set_clock(asd_r.clocks[i], ns);
  }
  pthread_mutex_unlock(&asd_r_clock_lock);
  pthread_mutex_lock(&asd_r_lock);
  for (entry_t * e = rp_live(KIND_TIMER); e != NULL; e = e->next) {
    rp_report_next(e);
  }
  rp_send("time\t%llu", seq);
  ++asd_r.wake;
  pthread_cond_broadcast(&asd_r_cond);
  pthread_mutex_unlock(&asd_r_lock);
}

bool asd_replay_drops_time_override(void)
{
  return !tls_setting_time;
}

// ---- clocks --------------------------------------------------------------------------

rcl_clock_t * asd_replay_timer_clock(rcl_clock_t * clock, bool * swapped)
{
  *swapped = false;
  if (clock == NULL) {
    return clock;
  }
  if (clock->type == RCL_ROS_TIME) {
    bool enabled = false;
    if (rcl_is_enabled_ros_time_override(clock, &enabled) == RCL_RET_OK && !enabled) {
      rcl_ret_t ret = rcl_enable_ros_time_override(clock);
      (void)ret;
    }
    return clock;
  }
  if (asd_r.wall_free) {
    return clock;
  }
  pthread_mutex_lock(&asd_r_clock_lock);
  if (!asd_r.own_clock_ready) {
    rcl_allocator_t allocator = rcl_get_default_allocator();
    // rcl_clock_init is hooked: a ROS clock is driver-timed from its creation.
    asd_r.own_clock_ready = rcl_clock_init(RCL_ROS_TIME, &asd_r.own_clock, &allocator) == RCL_RET_OK;
  }
  pthread_mutex_unlock(&asd_r_clock_lock);
  if (!asd_r.own_clock_ready) {
    return clock;
  }
  *swapped = true;
  return &asd_r.own_clock;
}

void asd_replay_clock_enabled(rcl_clock_t * clock)
{
  pthread_mutex_lock(&asd_r_clock_lock);
  bool known = false;
  for (uint32_t i = 0; i < asd_r.clock_count; ++i) {
    known = known || asd_r.clocks[i] == clock;
  }
  if (!known) {
    if (asd_r.clock_count == asd_r.clock_capacity) {
      uint32_t capacity = asd_r.clock_capacity != 0 ? 2 * asd_r.clock_capacity : 64;
      rcl_clock_t ** clocks = realloc(asd_r.clocks, capacity * sizeof(rcl_clock_t *));
      if (clocks == NULL) {
        pthread_mutex_unlock(&asd_r_clock_lock);
        return;
      }
      asd_r.clocks = clocks;
      asd_r.clock_capacity = capacity;
    }
    asd_r.clocks[asd_r.clock_count++] = clock;
    if (asd_r.time_set) {
      set_clock(clock, asd_r.time_ns);
    }
  }
  pthread_mutex_unlock(&asd_r_clock_lock);
}

void asd_replay_clock_gone(rcl_clock_t * clock)
{
  pthread_mutex_lock(&asd_r_clock_lock);
  for (uint32_t i = 0; i < asd_r.clock_count; ++i) {
    if (asd_r.clocks[i] == clock) {
      asd_r.clocks[i] = asd_r.clocks[--asd_r.clock_count];
      break;
    }
  }
  pthread_mutex_unlock(&asd_r_clock_lock);
}

void asd_replay_clock_lock(void)
{
  pthread_mutex_lock(&asd_r_clock_lock);
}

void asd_replay_clock_unlock(void)
{
  pthread_mutex_unlock(&asd_r_clock_lock);
}
