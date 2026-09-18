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

// LD_PRELOAD interposer over the rcl C API. Every hook forwards to the real
// function through dlsym(RTLD_NEXT) and appends one fixed record; init hooks
// append a name-table line. No hook inspects a message or holds a lock on the
// record path. Tracing is armed by ASD_TRACE_DIR; without it every hook is a
// plain forward.

#include <dlfcn.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

#include <rcl/node.h>
#include <rcl/publisher.h>
#include <rcl/subscription.h>
#include <rcl/time.h>
#include <rcl/timer.h>
#include <rmw/rmw.h>

#include "writer.h"

// ---- state -------------------------------------------------------------------

enum tracer_state { TRACER_UNSET = 0, TRACER_OFF = 1, TRACER_ON = 2 };

static asd_writer_t g_writer;
static int g_state = TRACER_UNSET;
static pthread_mutex_t g_state_lock = PTHREAD_MUTEX_INITIALIZER;
static __thread uint32_t tls_tid = 0;

static uint32_t current_tid(void)
{
  if (tls_tid == 0) {
    tls_tid = (uint32_t)syscall(SYS_gettid);
  }
  return tls_tid;
}

// The file belongs to one pid; a forked child starts over on its first hook.
static void on_fork_child(void)
{
  g_state = TRACER_UNSET;
  tls_tid = 0;
  memset(&g_writer, 0, sizeof(g_writer));
}

static void open_writer_locked(void)
{
  const char * dir = getenv(ASD_TRACE_DIR_ENV);
  if (dir == NULL || dir[0] == '\0') {
    g_state = TRACER_OFF;
    return;
  }
  uint64_t capacity = ASD_TRACE_DEFAULT_CAPACITY;
  const char * cap = getenv(ASD_TRACE_CAPACITY_ENV);
  if (cap != NULL && cap[0] != '\0') {
    unsigned long long parsed = strtoull(cap, NULL, 10);
    if (parsed > 0) {
      capacity = parsed;
    }
  }
  if (asd_writer_open(&g_writer, dir, (uint32_t)getpid(), capacity) != 0) {
    fprintf(stderr, "[asd_tracer] cannot open trace files in %s; tracing disabled\n", dir);
    g_state = TRACER_OFF;
    return;
  }
  g_state = TRACER_ON;
}

static inline bool tracing(void)
{
  int state = __atomic_load_n(&g_state, __ATOMIC_ACQUIRE);
  if (state == TRACER_UNSET) {
    pthread_mutex_lock(&g_state_lock);
    if (g_state == TRACER_UNSET) {
      open_writer_locked();
      __atomic_store_n(&g_state, g_state, __ATOMIC_RELEASE);
    }
    state = g_state;
    pthread_mutex_unlock(&g_state_lock);
  }
  return state == TRACER_ON;
}

__attribute__((constructor)) static void tracer_init(void)
{
  pthread_atfork(NULL, NULL, on_fork_child);
  (void)tracing();
}

// Exit only turns the hooks off: a spinner thread may still be inside one, and
// the shared mapping and the flushed name table are complete without a close.
__attribute__((destructor)) static void tracer_fini(void)
{
  int on = TRACER_ON;
  __atomic_compare_exchange_n(&g_state, &on, TRACER_OFF, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
}

// ---- record helpers -----------------------------------------------------------

static void record_take(
  const rcl_subscription_t * subscription, const rmw_message_info_t * info, uint64_t t_ns,
  uint8_t flags)
{
  asd_trace_record_t * r = asd_writer_claim(&g_writer);
  if (r == NULL) {
    return;
  }
  r->flags = flags;
  r->reserved = 0;
  r->tid = current_tid();
  r->t_ns = t_ns;
  r->handle = (uint64_t)(uintptr_t)subscription;
  if (info != NULL) {
    r->t2_ns = (uint64_t)info->source_timestamp;
    memcpy(r->gid, info->publisher_gid.data, ASD_TRACE_GID_SIZE);
    r->seq = info->publication_sequence_number;
    if (info->from_intra_process) {
      r->flags |= ASD_FLAG_FROM_INTRA;
    }
  } else {
    r->t2_ns = 0;
    memset(r->gid, 0, ASD_TRACE_GID_SIZE);
    r->seq = 0;
    r->flags |= ASD_FLAG_NO_INFO;
  }
  asd_writer_commit(r, ASD_REC_TAKE);
}

static void record_publish(const rcl_publisher_t * publisher, uint64_t t_in, uint64_t t_out, uint8_t flags)
{
  asd_trace_record_t * r = asd_writer_claim(&g_writer);
  if (r == NULL) {
    return;
  }
  r->flags = flags;
  r->reserved = 0;
  r->tid = current_tid();
  r->t_ns = t_in;
  r->t2_ns = t_out;
  r->handle = (uint64_t)(uintptr_t)publisher;
  memset(r->gid, 0, ASD_TRACE_GID_SIZE);
  r->seq = 0;
  asd_writer_commit(r, ASD_REC_PUBLISH);
}

static void record_timer(const rcl_timer_t * timer, uint64_t t_ns)
{
  asd_trace_record_t * r = asd_writer_claim(&g_writer);
  if (r == NULL) {
    return;
  }
  r->flags = 0;
  r->reserved = 0;
  r->tid = current_tid();
  r->t_ns = t_ns;
  r->t2_ns = 0;
  r->handle = (uint64_t)(uintptr_t)timer;
  memset(r->gid, 0, ASD_TRACE_GID_SIZE);
  r->seq = 0;
  asd_writer_commit(r, ASD_REC_TIMER);
}

static void write_clock_record(const rcl_clock_t * clock, uint64_t t_ns, int64_t ros_ns, uint8_t flags)
{
  asd_trace_record_t * r = asd_writer_claim(&g_writer);
  if (r == NULL) {
    return;
  }
  r->flags = flags;
  r->reserved = 0;
  r->tid = current_tid();
  r->t_ns = t_ns;
  r->t2_ns = (uint64_t)ros_ns;
  r->handle = (uint64_t)(uintptr_t)clock;
  memset(r->gid, 0, ASD_TRACE_GID_SIZE);
  r->seq = 0;
  asd_writer_commit(r, ASD_REC_CLOCK);
}

// One record per distinct ROS time value: every node clock of a process receives
// the same /clock message, and the mapping only needs the first. A value held for
// longer than twice the gap before it (a paused /clock keeps publishing its frozen
// value) also records the last wall time it was seen, so the reader keeps the
// plateau flat.
static void record_clock(const rcl_clock_t * clock, uint64_t t_ns, int64_t ros_ns)
{
  static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
  static int64_t held_ros_ns = -1;
  static uint64_t held_first_ns = 0;
  static uint64_t held_last_ns = 0;
  static uint64_t gap_before_ns = 0;

  pthread_mutex_lock(&lock);
  if (ros_ns == held_ros_ns) {
    if (t_ns > held_last_ns) {
      held_last_ns = t_ns;
    }
    pthread_mutex_unlock(&lock);
    return;
  }
  int64_t previous = held_ros_ns;
  uint64_t plateau_end = 0;
  if (previous >= 0 && gap_before_ns > 0 && held_last_ns - held_first_ns > 2 * gap_before_ns) {
    plateau_end = held_last_ns;
  }
  gap_before_ns = (previous >= 0 && t_ns > held_last_ns) ? t_ns - held_last_ns : 0;
  held_ros_ns = ros_ns;
  held_first_ns = t_ns;
  held_last_ns = t_ns;
  pthread_mutex_unlock(&lock);

  if (plateau_end != 0) {
    write_clock_record(clock, plateau_end, previous, ASD_FLAG_CLOCK_LAST);
  }
  write_clock_record(clock, t_ns, ros_ns, 0);
}

static void gid_hex(const rmw_gid_t * gid, char out[ASD_TRACE_GID_SIZE * 2 + 1])
{
  static const char digits[] = "0123456789abcdef";
  for (unsigned i = 0; i < ASD_TRACE_GID_SIZE; ++i) {
    out[2 * i] = digits[gid->data[i] >> 4];
    out[2 * i + 1] = digits[gid->data[i] & 0xf];
  }
  out[ASD_TRACE_GID_SIZE * 2] = '\0';
}

static void name_endpoint(
  const char * kind, const void * handle, const rcl_node_t * node, const char * topic,
  const rmw_gid_t * gid)
{
  const char * fqn = node != NULL ? rcl_node_get_fully_qualified_name(node) : NULL;
  char gid_text[ASD_TRACE_GID_SIZE * 2 + 1] = "";
  if (gid != NULL) {
    gid_hex(gid, gid_text);
  }
  char line[4096];
  snprintf(
    line, sizeof(line), "%llu\t%s\t%llx\t%s\t%s\t%s", (unsigned long long)asd_now_realtime_ns(), kind,
    (unsigned long long)(uintptr_t)handle, fqn != NULL ? fqn : "?", topic != NULL ? topic : "?",
    gid_text);
  asd_writer_name(&g_writer, line);
}

static void name_timer(const void * handle, int64_t period)
{
  char line[256];
  snprintf(
    line, sizeof(line), "%llu\ttimer\t%llx\t%lld", (unsigned long long)asd_now_realtime_ns(),
    (unsigned long long)(uintptr_t)handle, (long long)period);
  asd_writer_name(&g_writer, line);
}

// ---- real-function lookup ---------------------------------------------------------

#define REAL(name)                                                       \
  static __typeof__(name) * real_##name = NULL;                          \
  if (real_##name == NULL) {                                             \
    real_##name = (__typeof__(name) *)dlsym(RTLD_NEXT, #name);           \
    if (real_##name == NULL) {                                           \
      return RCL_RET_ERROR;                                              \
    }                                                                    \
  }

// ---- publish hooks ------------------------------------------------------------------

rcl_ret_t rcl_publish(
  const rcl_publisher_t * publisher, const void * ros_message, rmw_publisher_allocation_t * allocation)
{
  REAL(rcl_publish);
  if (!tracing()) {
    return real_rcl_publish(publisher, ros_message, allocation);
  }
  uint64_t t_in = asd_now_realtime_ns();
  rcl_ret_t ret = real_rcl_publish(publisher, ros_message, allocation);
  if (ret == RCL_RET_OK) {
    record_publish(publisher, t_in, asd_now_realtime_ns(), 0);
  }
  return ret;
}

rcl_ret_t rcl_publish_serialized_message(
  const rcl_publisher_t * publisher, const rcl_serialized_message_t * serialized_message,
  rmw_publisher_allocation_t * allocation)
{
  REAL(rcl_publish_serialized_message);
  if (!tracing()) {
    return real_rcl_publish_serialized_message(publisher, serialized_message, allocation);
  }
  uint64_t t_in = asd_now_realtime_ns();
  rcl_ret_t ret = real_rcl_publish_serialized_message(publisher, serialized_message, allocation);
  if (ret == RCL_RET_OK) {
    record_publish(publisher, t_in, asd_now_realtime_ns(), ASD_FLAG_SERIALIZED);
  }
  return ret;
}

rcl_ret_t rcl_publish_loaned_message(
  const rcl_publisher_t * publisher, void * ros_message, rmw_publisher_allocation_t * allocation)
{
  REAL(rcl_publish_loaned_message);
  if (!tracing()) {
    return real_rcl_publish_loaned_message(publisher, ros_message, allocation);
  }
  uint64_t t_in = asd_now_realtime_ns();
  rcl_ret_t ret = real_rcl_publish_loaned_message(publisher, ros_message, allocation);
  if (ret == RCL_RET_OK) {
    record_publish(publisher, t_in, asd_now_realtime_ns(), ASD_FLAG_LOANED);
  }
  return ret;
}

// ---- take hooks ----------------------------------------------------------------------

rcl_ret_t rcl_take(
  const rcl_subscription_t * subscription, void * ros_message, rmw_message_info_t * message_info,
  rmw_subscription_allocation_t * allocation)
{
  REAL(rcl_take);
  if (!tracing()) {
    return real_rcl_take(subscription, ros_message, message_info, allocation);
  }
  rmw_message_info_t local;
  rmw_message_info_t * info = message_info != NULL ? message_info : &local;
  rcl_ret_t ret = real_rcl_take(subscription, ros_message, info, allocation);
  if (ret == RCL_RET_OK) {
    record_take(subscription, info, asd_now_realtime_ns(), 0);
  }
  return ret;
}

rcl_ret_t rcl_take_serialized_message(
  const rcl_subscription_t * subscription, rcl_serialized_message_t * serialized_message,
  rmw_message_info_t * message_info, rmw_subscription_allocation_t * allocation)
{
  REAL(rcl_take_serialized_message);
  if (!tracing()) {
    return real_rcl_take_serialized_message(subscription, serialized_message, message_info, allocation);
  }
  rmw_message_info_t local;
  rmw_message_info_t * info = message_info != NULL ? message_info : &local;
  rcl_ret_t ret = real_rcl_take_serialized_message(subscription, serialized_message, info, allocation);
  if (ret == RCL_RET_OK) {
    record_take(subscription, info, asd_now_realtime_ns(), ASD_FLAG_SERIALIZED);
  }
  return ret;
}

rcl_ret_t rcl_take_loaned_message(
  const rcl_subscription_t * subscription, void ** loaned_message, rmw_message_info_t * message_info,
  rmw_subscription_allocation_t * allocation)
{
  REAL(rcl_take_loaned_message);
  if (!tracing()) {
    return real_rcl_take_loaned_message(subscription, loaned_message, message_info, allocation);
  }
  rmw_message_info_t local;
  rmw_message_info_t * info = message_info != NULL ? message_info : &local;
  rcl_ret_t ret = real_rcl_take_loaned_message(subscription, loaned_message, info, allocation);
  if (ret == RCL_RET_OK) {
    record_take(subscription, info, asd_now_realtime_ns(), ASD_FLAG_LOANED);
  }
  return ret;
}

// ---- timer hooks ----------------------------------------------------------------------

rcl_ret_t rcl_timer_call(rcl_timer_t * timer)
{
  REAL(rcl_timer_call);
  if (!tracing()) {
    return real_rcl_timer_call(timer);
  }
  rcl_ret_t ret = real_rcl_timer_call(timer);
  if (ret == RCL_RET_OK) {
    record_timer(timer, asd_now_realtime_ns());
  }
  return ret;
}

rcl_ret_t rcl_timer_init(
  rcl_timer_t * timer, rcl_clock_t * clock, rcl_context_t * context, int64_t period,
  const rcl_timer_callback_t callback, rcl_allocator_t allocator)
{
  REAL(rcl_timer_init);
  rcl_ret_t ret = real_rcl_timer_init(timer, clock, context, period, callback, allocator);
  if (ret == RCL_RET_OK && tracing()) {
    name_timer(timer, period);
  }
  return ret;
}

// Jazzy and later create timers through this entry point; Humble has no such symbol,
// and a process that never calls it never reaches the NULL forward.
rcl_ret_t rcl_timer_init2(
  rcl_timer_t * timer, rcl_clock_t * clock, rcl_context_t * context, int64_t period,
  const rcl_timer_callback_t callback, rcl_allocator_t allocator, bool autostart)
{
  typedef rcl_ret_t (*init2_fn)(
    rcl_timer_t *, rcl_clock_t *, rcl_context_t *, int64_t, const rcl_timer_callback_t, rcl_allocator_t,
    bool);
  static init2_fn real_init2 = NULL;
  if (real_init2 == NULL) {
    real_init2 = (init2_fn)dlsym(RTLD_NEXT, "rcl_timer_init2");
    if (real_init2 == NULL) {
      return RCL_RET_ERROR;
    }
  }
  rcl_ret_t ret = real_init2(timer, clock, context, period, callback, allocator, autostart);
  if (ret == RCL_RET_OK && tracing()) {
    name_timer(timer, period);
  }
  return ret;
}

// ---- clock hook -----------------------------------------------------------------------

// rclcpp's TimeSource sets the override on every /clock message while use_sim_time
// is on; the records map wall time to ROS time for the analysis.
rcl_ret_t rcl_set_ros_time_override(rcl_clock_t * clock, rcl_time_point_value_t time_value)
{
  REAL(rcl_set_ros_time_override);
  rcl_ret_t ret = real_rcl_set_ros_time_override(clock, time_value);
  if (ret == RCL_RET_OK && tracing()) {
    record_clock(clock, asd_now_realtime_ns(), time_value);
  }
  return ret;
}

// ---- endpoint init hooks ---------------------------------------------------------------

rcl_ret_t rcl_publisher_init(
  rcl_publisher_t * publisher, const rcl_node_t * node, const rosidl_message_type_support_t * type_support,
  const char * topic_name, const rcl_publisher_options_t * options)
{
  REAL(rcl_publisher_init);
  rcl_ret_t ret = real_rcl_publisher_init(publisher, node, type_support, topic_name, options);
  if (ret == RCL_RET_OK && tracing()) {
    rmw_gid_t gid;
    memset(&gid, 0, sizeof(gid));
    const rmw_gid_t * gid_ptr = NULL;
    rmw_publisher_t * rmw_pub = rcl_publisher_get_rmw_handle(publisher);
    if (rmw_pub != NULL && rmw_get_gid_for_publisher(rmw_pub, &gid) == RMW_RET_OK) {
      gid_ptr = &gid;
    }
    name_endpoint("pub", publisher, node, rcl_publisher_get_topic_name(publisher), gid_ptr);
  }
  return ret;
}

rcl_ret_t rcl_subscription_init(
  rcl_subscription_t * subscription, const rcl_node_t * node,
  const rosidl_message_type_support_t * type_support, const char * topic_name,
  const rcl_subscription_options_t * options)
{
  REAL(rcl_subscription_init);
  rcl_ret_t ret = real_rcl_subscription_init(subscription, node, type_support, topic_name, options);
  if (ret == RCL_RET_OK && tracing()) {
    name_endpoint("sub", subscription, node, rcl_subscription_get_topic_name(subscription), NULL);
  }
  return ret;
}
