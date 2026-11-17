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

// One process's link to the replay driver, a Unix stream socket carrying
// tab-separated lines (the driver side is autoware_system_designer_runtime/
// _impl/replay/link.py).
//
//   process -> driver
//     hello  pid
//     node   fqn
//     ep     id kind(p|s) node topic reliability durability history depth
//     timer  id node period_ns clock(r: ROS, w: swapped from a wall clock, f: free wall clock) next_ns
//     fini   id
//     waited id                 the endpoint sits in a wait set: its callbacks are gated
//     ready  id / unready id    it has work, or lost it without a callback
//     take   id gated           a message was taken; gated: by the thread holding the grant.
//                               The subscription is not ready until a later `ready`
//     fire   id gated           a timer was called; it is not ready until a later `ready`
//     next   id next_ns         a timer's next call time, -1 when canceled
//     pub    id gated
//     done   id                 the granted callback returned to its executor
//     lost   id                 a message in flight never arrived
//     time   seq                the clocks hold the time of `time seq`
//     match  id count / matched seq
//   driver -> process
//     time   seq ns             set every ROS clock
//     grant  id                 run one callback of the endpoint; a second one of a process
//                               is granted while the first waits on it
//     expect id                 a message was published to the subscription
//     match  seq                report the matched subscriptions of every publisher
//     free                      stop gating
//
// An executor learns of work through rcl_wait, so the gate sits there: the hook
// reports which subscriptions and timers are ready, hides all of them from the
// executor, and hands back the one the driver grants. The thread that received a
// grant holds it until its callback is over: the return of rclcpp's
// Executor::execute_any_executable, else its next rcl_wait. Each thread holds one
// grant at most, TOKENS threads at a time.

#include "replay.h"

#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include <rcl/allocator.h>
#include <rcl/publisher.h>
#include <rcl/timer.h>

#define SOCKET_ENV "ASD_REPLAY_SOCKET"
#define START_ENV "ASD_REPLAY_START_NS"
// "free" leaves timers of a steady or system clock on that clock, ungated.
#define WALL_TIMERS_ENV "ASD_REPLAY_WALL_TIMERS"

// Longest stay in rcl_wait with ready work and no grant; bounds how late the executor sees new work.
#define HOLD_NS 2000000ll
// A take with a message in flight tries again every TAKE_RETRY_US, TAKE_RETRIES times at most.
#define TAKE_RETRY_US 200
#define TAKE_RETRIES 10000u
// Threads of a process that hold a grant at once.
#define TOKENS 4

enum { STATE_UNSET = 0, STATE_OFF = 1, STATE_ON = 2 };

typedef struct entry {
  const void * handle;  // NULL once finalized
  char kind;            // 'p', 's', 't'
  bool gated;
  bool waited;
  bool ready;
  uint32_t depth;       // subscription: keep-last depth, 0 without a bound
  uint32_t pending;     // subscription: messages published to it and not taken
  uint32_t takes;       // messages taken, or calls of the timer
  char * node;
  rcl_clock_t * clock;  // timer
  int64_t next_ns;      // timer: the next call time last reported
} entry_t;

typedef struct replay {
  int state;
  int fd;
  bool gating;
  bool wall_free;
  // Entries by id; id 0 is unused. `table` maps a handle to its id by open addressing.
  entry_t * entries;
  uint32_t count;
  uint32_t capacity;
  uint32_t * table;
  uint32_t table_size;
  uint32_t table_used;
  // The grant the driver issued, and the threads running the grants taken.
  uint32_t grant;
  uint32_t token_id[TOKENS];
  uint32_t token_tid[TOKENS];
  uint64_t wake;
  rcl_clock_t ** clocks;
  uint32_t clock_count;
  uint32_t clock_capacity;
  bool time_set;
  int64_t time_ns;
  rcl_clock_t own_clock;
  bool own_clock_ready;
} replay_t;

static replay_t r;
static pthread_mutex_t r_state_lock = PTHREAD_MUTEX_INITIALIZER;
// Entries, grant and socket writes.
static pthread_mutex_t r_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t r_cond;
// Clock list and every change of a clock's override or jump callbacks; taken before r_lock.
static pthread_mutex_t r_clock_lock;

static __thread uint32_t tls_tid = 0;
static __thread bool tls_setting_time = false;
// Depth of rclcpp executor callbacks on this thread; a wait inside one is part of the callback.
static __thread int tls_callback_depth = 0;
static __thread char tls_node[512] = "";
static __thread const void ** tls_snapshot = NULL;
static __thread bool * tls_gated = NULL;
// Takes of each slot's entry when the wait began; a take during the wait voids what it saw.
static __thread uint32_t * tls_takes = NULL;
static __thread size_t tls_snapshot_size = 0;

#define TABLE_TOMB UINT32_MAX

static uint32_t self_tid(void)
{
  if (tls_tid == 0) {
    tls_tid = (uint32_t)syscall(SYS_gettid);
  }
  return tls_tid;
}

// Slot of the grant `tid` holds, TOKENS when it holds none; tid 0 finds a free slot.
static int token_slot(uint32_t tid)
{
  int slot = 0;
  while (slot < TOKENS && r.token_tid[slot] != tid) {
    ++slot;
  }
  return slot;
}

// Id of the endpoint whose grant the calling thread holds, else 0.
static uint32_t own_token(void)
{
  int slot = token_slot(self_tid());
  return slot < TOKENS ? r.token_id[slot] : 0;
}

// The condition variable waits on the monotonic clock; the clock lock is taken again by the hooks
// a time jump callback reaches.
__attribute__((constructor)) static void init_locks(void)
{
  pthread_condattr_t cond_attr;
  pthread_condattr_init(&cond_attr);
  pthread_condattr_setclock(&cond_attr, CLOCK_MONOTONIC);
  pthread_cond_init(&r_cond, &cond_attr);
  pthread_condattr_destroy(&cond_attr);
  pthread_mutexattr_t mutex_attr;
  pthread_mutexattr_init(&mutex_attr);
  pthread_mutexattr_settype(&mutex_attr, PTHREAD_MUTEX_RECURSIVE);
  pthread_mutex_init(&r_clock_lock, &mutex_attr);
  pthread_mutexattr_destroy(&mutex_attr);
}

// ---- socket ----------------------------------------------------------------------

// Called with r_lock held. A failed write ends the gating: the process runs free.
static void send_line(const char * format, ...)
{
  if (r.fd < 0) {
    return;
  }
  char line[4608];
  va_list args;
  va_start(args, format);
  int length = vsnprintf(line, sizeof(line) - 1, format, args);
  va_end(args);
  if (length < 0) {
    return;
  }
  if ((size_t)length > sizeof(line) - 2) {
    length = (int)sizeof(line) - 2;
  }
  line[length++] = '\n';
  for (int sent = 0; sent < length;) {
    ssize_t n = send(r.fd, line + sent, (size_t)(length - sent), MSG_NOSIGNAL);
    if (n < 0) {
      if (errno == EINTR) {
        continue;
      }
      r.gating = false;
      pthread_cond_broadcast(&r_cond);
      return;
    }
    sent += (int)n;
  }
}

// ---- entries ---------------------------------------------------------------------

static uint32_t hash_handle(const void * handle)
{
  uint64_t x = (uint64_t)(uintptr_t)handle;
  x ^= x >> 33;
  x *= 0xff51afd7ed558ccdull;
  x ^= x >> 33;
  return (uint32_t)x;
}

static void table_put(const void * handle, uint32_t id)
{
  uint32_t mask = r.table_size - 1;
  uint32_t i = hash_handle(handle) & mask;
  while (r.table[i] != 0 && r.table[i] != TABLE_TOMB) {
    i = (i + 1) & mask;
  }
  if (r.table[i] == 0) {
    ++r.table_used;
  }
  r.table[i] = id;
}

static bool table_rebuild(void)
{
  uint32_t live = 0;
  for (uint32_t id = 1; id < r.count; ++id) {
    live += r.entries[id].handle != NULL;
  }
  uint32_t size = 256;
  while (size < 4 * (live + 1)) {
    size *= 2;
  }
  uint32_t * table = calloc(size, sizeof(uint32_t));
  if (table == NULL) {
    return false;
  }
  free(r.table);
  r.table = table;
  r.table_size = size;
  r.table_used = 0;
  for (uint32_t id = 1; id < r.count; ++id) {
    if (r.entries[id].handle != NULL) {
      table_put(r.entries[id].handle, id);
    }
  }
  return true;
}

static uint32_t find_id(const void * handle)
{
  if (r.table == NULL || handle == NULL) {
    return 0;
  }
  uint32_t mask = r.table_size - 1;
  for (uint32_t i = hash_handle(handle) & mask; r.table[i] != 0; i = (i + 1) & mask) {
    uint32_t id = r.table[i];
    if (id != TABLE_TOMB && r.entries[id].handle == handle) {
      return id;
    }
  }
  return 0;
}

// An rcl object initialized on the caller's stack is copied to its final place afterwards
// (rcl's own rosout publisher is), so its address names nothing once the caller returns.
static bool on_stack(const void * handle)
{
  pthread_attr_t attr;
  void * base = NULL;
  size_t size = 0;
  bool inside = false;
  if (pthread_getattr_np(pthread_self(), &attr) != 0) {
    return false;
  }
  if (pthread_attr_getstack(&attr, &base, &size) == 0) {
    inside = (const char *)handle >= (const char *)base && (const char *)handle < (const char *)base + size;
  }
  pthread_attr_destroy(&attr);
  return inside;
}

// A new entry for `handle`; 0 when it cannot be tracked, and the endpoint stays ungated.
static uint32_t add_entry(const void * handle, char kind, const char * node)
{
  if (on_stack(handle)) {
    return 0;
  }
  if (r.count + 1 > r.capacity) {
    uint32_t capacity = r.capacity != 0 ? 2 * r.capacity : 256;
    entry_t * entries = realloc(r.entries, capacity * sizeof(entry_t));
    if (entries == NULL) {
      return 0;
    }
    r.entries = entries;
    r.capacity = capacity;
    if (r.count == 0) {
      r.count = 1;
    }
  }
  if (r.table == NULL || 2 * (r.table_used + 1) > r.table_size) {
    if (!table_rebuild()) {
      return 0;
    }
  }
  uint32_t id = r.count++;
  entry_t * e = &r.entries[id];
  memset(e, 0, sizeof(*e));
  e->handle = handle;
  e->kind = kind;
  e->node = strdup(node);
  e->next_ns = INT64_MIN;
  table_put(handle, id);
  return id;
}

static void drop_entry(uint32_t id)
{
  entry_t * e = &r.entries[id];
  uint32_t mask = r.table_size - 1;
  for (uint32_t i = hash_handle(e->handle) & mask; r.table[i] != 0; i = (i + 1) & mask) {
    if (r.table[i] == id) {
      r.table[i] = TABLE_TOMB;
      break;
    }
  }
  e->handle = NULL;
  free(e->node);
  e->node = NULL;
}

// ---- timers ------------------------------------------------------------------------

// Next call time of a timer on its own clock; -1 when it is canceled.
static int64_t timer_next(const entry_t * e)
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

// Called with r_lock held.
static void report_next(uint32_t id)
{
  entry_t * e = &r.entries[id];
  if (!e->gated) {
    return;
  }
  int64_t next = timer_next(e);
  if (next != e->next_ns) {
    e->next_ns = next;
    send_line("next\t%u\t%lld", id, (long long)next);
  }
}

// ---- time --------------------------------------------------------------------------

static void set_clock(rcl_clock_t * clock, int64_t ns)
{
  tls_setting_time = true;
  rcl_ret_t ret = rcl_set_ros_time_override(clock, ns);
  tls_setting_time = false;
  (void)ret;
}

static void apply_time(int64_t ns)
{
  pthread_mutex_lock(&r_clock_lock);
  r.time_ns = ns;
  r.time_set = true;
  for (uint32_t i = 0; i < r.clock_count; ++i) {
    set_clock(r.clocks[i], ns);
  }
  pthread_mutex_unlock(&r_clock_lock);
}

// ---- driver commands -----------------------------------------------------------------

static void on_time(unsigned long long seq, long long ns)
{
  apply_time(ns);
  pthread_mutex_lock(&r_lock);
  for (uint32_t id = 1; id < r.count; ++id) {
    if (r.entries[id].handle != NULL && r.entries[id].kind == 't') {
      report_next(id);
    }
  }
  send_line("time\t%llu", seq);
  ++r.wake;
  pthread_cond_broadcast(&r_cond);
  pthread_mutex_unlock(&r_lock);
}

static void on_match(unsigned long long seq)
{
  pthread_mutex_lock(&r_lock);
  for (uint32_t id = 1; id < r.count; ++id) {
    const entry_t * e = &r.entries[id];
    if (e->handle == NULL || e->kind != 'p') {
      continue;
    }
    size_t matched = 0;
    if (rcl_publisher_get_subscription_count((const rcl_publisher_t *)e->handle, &matched) == RCL_RET_OK) {
      send_line("match\t%u\t%zu", id, matched);
    }
  }
  send_line("matched\t%llu", seq);
  pthread_mutex_unlock(&r_lock);
}

static void on_command(char * line)
{
  unsigned long long a = 0;
  long long b = 0;
  if (sscanf(line, "time\t%llu\t%lld", &a, &b) == 2) {
    on_time(a, b);
  } else if (sscanf(line, "match\t%llu", &a) == 1) {
    on_match(a);
  } else if (sscanf(line, "grant\t%llu", &a) == 1) {
    pthread_mutex_lock(&r_lock);
    r.grant = (uint32_t)a;
    pthread_cond_broadcast(&r_cond);
    pthread_mutex_unlock(&r_lock);
  } else if (sscanf(line, "expect\t%llu", &a) == 1) {
    pthread_mutex_lock(&r_lock);
    if (a > 0 && a < r.count && r.entries[a].handle != NULL) {
      entry_t * e = &r.entries[a];
      if (e->depth == 0 || e->pending < e->depth) {
        ++e->pending;
      }
    }
    pthread_mutex_unlock(&r_lock);
  } else if (strcmp(line, "free") == 0) {
    pthread_mutex_lock(&r_lock);
    r.gating = false;
    pthread_cond_broadcast(&r_cond);
    pthread_mutex_unlock(&r_lock);
  }
}

static void * reader_main(void * arg)
{
  (void)arg;
  char buffer[4096];
  size_t have = 0;
  for (;;) {
    ssize_t n = recv(r.fd, buffer + have, sizeof(buffer) - have - 1, 0);
    if (n < 0 && errno == EINTR) {
      continue;
    }
    if (n <= 0) {
      break;
    }
    have += (size_t)n;
    buffer[have] = '\0';
    char * line = buffer;
    for (char * end; (end = strchr(line, '\n')) != NULL; line = end + 1) {
      *end = '\0';
      on_command(line);
    }
    have = strlen(line);
    memmove(buffer, line, have);
    if (have == sizeof(buffer) - 1) {
      have = 0;
    }
  }
  pthread_mutex_lock(&r_lock);
  r.gating = false;
  pthread_cond_broadcast(&r_cond);
  pthread_mutex_unlock(&r_lock);
  return NULL;
}

// ---- arming ------------------------------------------------------------------------

static int connect_locked(void)
{
  const char * path = getenv(SOCKET_ENV);
  struct sockaddr_un address;
  if (path == NULL || path[0] == '\0' || strlen(path) >= sizeof(address.sun_path)) {
    return STATE_OFF;
  }
  int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (fd < 0) {
    return STATE_OFF;
  }
  memset(&address, 0, sizeof(address));
  address.sun_family = AF_UNIX;
  strcpy(address.sun_path, path);
  if (connect(fd, (const struct sockaddr *)&address, sizeof(address)) != 0) {
    fprintf(stderr, "[asd_tracer] no replay driver at %s: %s; running free\n", path, strerror(errno));
    close(fd);
    return STATE_OFF;
  }
  r.fd = fd;
  r.gating = true;
  const char * wall = getenv(WALL_TIMERS_ENV);
  r.wall_free = wall != NULL && strcmp(wall, "free") == 0;
  const char * start = getenv(START_ENV);
  if (start != NULL && start[0] != '\0') {
    r.time_ns = strtoll(start, NULL, 10);
    r.time_set = true;
  }
  pthread_mutex_lock(&r_lock);
  send_line("hello\t%d", (int)getpid());
  pthread_mutex_unlock(&r_lock);

  // The reader takes no signal: handlers stay on the process's own threads.
  sigset_t all, previous;
  sigfillset(&all);
  pthread_sigmask(SIG_SETMASK, &all, &previous);
  pthread_t thread;
  int created = pthread_create(&thread, NULL, reader_main, NULL);
  pthread_sigmask(SIG_SETMASK, &previous, NULL);
  if (created != 0) {
    close(fd);
    r.fd = -1;
    r.gating = false;
    return STATE_OFF;
  }
  pthread_detach(thread);
  return STATE_ON;
}

bool asd_replay_armed(void)
{
  int state = __atomic_load_n(&r.state, __ATOMIC_ACQUIRE);
  if (state == STATE_UNSET) {
    pthread_mutex_lock(&r_state_lock);
    if (r.state == STATE_UNSET) {
      __atomic_store_n(&r.state, connect_locked(), __ATOMIC_RELEASE);
    }
    state = r.state;
    pthread_mutex_unlock(&r_state_lock);
  }
  return state == STATE_ON;
}

// The link belongs to the parent; a child that reaches rcl connects for itself.
void asd_replay_at_fork_child(void)
{
  if (r.state == STATE_ON) {
    close(r.fd);
  }
  memset(&r, 0, sizeof(r));
  pthread_mutex_init(&r_state_lock, NULL);
  pthread_mutex_init(&r_lock, NULL);
  init_locks();
  tls_tid = 0;
}

// ---- endpoints -----------------------------------------------------------------------

void asd_replay_node(const rcl_node_t * node)
{
  const char * fqn = rcl_node_get_fully_qualified_name(node);
  snprintf(tls_node, sizeof(tls_node), "%s", fqn != NULL ? fqn : "?");
  pthread_mutex_lock(&r_lock);
  send_line("node\t%s", tls_node);
  pthread_mutex_unlock(&r_lock);
}

void asd_replay_endpoint(
  char kind, const void * handle, const rcl_node_t * node, const char * topic, const rmw_qos_profile_t * qos)
{
  const char * fqn = node != NULL ? rcl_node_get_fully_qualified_name(node) : NULL;
  if (fqn == NULL) {
    fqn = "?";
  }
  pthread_mutex_lock(&r_lock);
  uint32_t id = add_entry(handle, kind, fqn);
  if (id != 0) {
    entry_t * e = &r.entries[id];
    e->gated = kind == 's';
    size_t depth = 0;
    int reliability = 0, durability = 0, history = 0;
    if (qos != NULL) {
      reliability = (int)qos->reliability;
      durability = (int)qos->durability;
      history = (int)qos->history;
      depth = qos->depth;
      if (qos->history != RMW_QOS_POLICY_HISTORY_KEEP_ALL) {
        e->depth = depth > 0 ? (uint32_t)depth : 1;
      }
    }
    send_line(
      "ep\t%u\t%c\t%s\t%s\t%d\t%d\t%d\t%zu", id, kind, fqn, topic != NULL ? topic : "?", reliability, durability,
      history, depth);
  }
  pthread_mutex_unlock(&r_lock);
}

void asd_replay_timer(const void * timer, rcl_clock_t * clock, int64_t period_ns, bool swapped)
{
  pthread_mutex_lock(&r_lock);
  // A timer created inside a callback belongs to that callback's node.
  const char * node = tls_node[0] != '\0' ? tls_node : "?";
  uint32_t token = own_token();
  if (token != 0 && r.entries[token].node != NULL) {
    node = r.entries[token].node;
  }
  uint32_t id = add_entry(timer, 't', node);
  if (id != 0) {
    entry_t * e = &r.entries[id];
    e->clock = clock;
    e->gated = clock->type == RCL_ROS_TIME;
    char clock_kind = swapped ? 'w' : (e->gated ? 'r' : 'f');
    e->next_ns = e->gated ? timer_next(e) : -1;
    send_line(
      "timer\t%u\t%s\t%lld\t%c\t%lld", id, e->node, (long long)period_ns, clock_kind, (long long)e->next_ns);
  }
  pthread_mutex_unlock(&r_lock);
}

void asd_replay_release(const void * handle)
{
  pthread_mutex_lock(&r_lock);
  uint32_t id = find_id(handle);
  if (id != 0) {
    if (r.grant == id) {
      r.grant = 0;
    }
    drop_entry(id);
    send_line("fini\t%u", id);
  }
  pthread_mutex_unlock(&r_lock);
}

// ---- callbacks -----------------------------------------------------------------------

static bool reserve_snapshot(size_t size)
{
  if (size <= tls_snapshot_size) {
    return true;
  }
  const void ** snapshot = realloc(tls_snapshot, size * sizeof(void *));
  if (snapshot == NULL) {
    return false;
  }
  tls_snapshot = snapshot;
  bool * gated = realloc(tls_gated, size * sizeof(bool));
  if (gated == NULL) {
    return false;
  }
  tls_gated = gated;
  uint32_t * takes = realloc(tls_takes, size * sizeof(uint32_t));
  if (takes == NULL) {
    return false;
  }
  tls_takes = takes;
  tls_snapshot_size = size;
  return true;
}

static bool any_ready(const void * const * handles, size_t size)
{
  for (size_t i = 0; i < size; ++i) {
    if (handles[i] != NULL) {
      return true;
    }
  }
  return false;
}

// The entry of slot `i` of the snapshot: subscriptions first, then timers.
static const void ** slot_of(rcl_wait_set_t * wait_set, size_t i)
{
  size_t subscriptions = wait_set->size_of_subscriptions;
  return i < subscriptions ? (const void **)&wait_set->subscriptions[i]
                           : (const void **)&wait_set->timers[i - subscriptions];
}

// Ends the grant `tid` holds: its callback is over. Called with r_lock held.
static void release_grant(uint32_t tid)
{
  int slot = token_slot(tid);
  if (slot == TOKENS) {
    return;
  }
  send_line("done\t%u", r.token_id[slot]);
  r.token_tid[slot] = 0;
  r.token_id[slot] = 0;
  ++r.wake;
  pthread_cond_broadcast(&r_cond);
}

void asd_replay_callback_enter(void)
{
  ++tls_callback_depth;
}

void asd_replay_callback_exit(void)
{
  if (--tls_callback_depth > 0) {
    return;
  }
  pthread_mutex_lock(&r_lock);
  release_grant(self_tid());
  pthread_mutex_unlock(&r_lock);
}

rcl_ret_t asd_replay_wait(asd_wait_fn real, rcl_wait_set_t * wait_set, int64_t timeout)
{
  if (wait_set == NULL || !__atomic_load_n(&r.gating, __ATOMIC_RELAXED)) {
    return real(wait_set, timeout);
  }
  uint32_t tid = self_tid();
  size_t slots = wait_set->size_of_subscriptions + wait_set->size_of_timers;
  if (!reserve_snapshot(slots)) {
    return real(wait_set, timeout);
  }

  pthread_mutex_lock(&r_lock);
  if (tls_callback_depth == 0) {
    release_grant(tid);
  }
  for (size_t i = 0; i < slots; ++i) {
    tls_snapshot[i] = *slot_of(wait_set, i);
    uint32_t id = find_id(tls_snapshot[i]);
    tls_takes[i] = id != 0 ? r.entries[id].takes : 0;
    if (id != 0 && r.entries[id].gated && !r.entries[id].waited) {
      r.entries[id].waited = true;
      send_line("waited\t%u", id);
    }
  }
  pthread_mutex_unlock(&r_lock);

  rcl_ret_t ret = real(wait_set, timeout);
  if (ret != RCL_RET_OK && ret != RCL_RET_TIMEOUT) {
    return ret;
  }

  pthread_mutex_lock(&r_lock);
  if (!r.gating) {
    pthread_mutex_unlock(&r_lock);
    return ret;
  }
  bool other = any_ready((const void * const *)wait_set->guard_conditions, wait_set->size_of_guard_conditions) ||
    any_ready((const void * const *)wait_set->clients, wait_set->size_of_clients) ||
    any_ready((const void * const *)wait_set->services, wait_set->size_of_services) ||
    any_ready((const void * const *)wait_set->events, wait_set->size_of_events);
  size_t held = 0;
  bool stale = false;
  for (size_t i = 0; i < slots; ++i) {
    tls_gated[i] = false;
    if (tls_snapshot[i] == NULL) {
      continue;
    }
    bool ready = *slot_of(wait_set, i) != NULL;
    uint32_t id = find_id(tls_snapshot[i]);
    if (id == 0 || !r.entries[id].gated) {
      other = other || ready;
      continue;
    }
    tls_gated[i] = true;
    entry_t * e = &r.entries[id];
    if (ready && e->takes != tls_takes[i]) {
      *slot_of(wait_set, i) = NULL;
      stale = true;
      continue;
    }
    if (ready != e->ready) {
      e->ready = ready;
      send_line(ready ? "ready\t%u" : "unready\t%u", id);
      if (!ready && r.grant == id) {
        r.grant = 0;
      }
    }
    held += ready;
  }
  if (held == 0) {
    pthread_mutex_unlock(&r_lock);
    return ret;
  }

  int64_t hold_ns = (other || stale || timeout == 0) ? 0 : HOLD_NS;
  if (timeout > 0 && timeout < hold_ns) {
    hold_ns = timeout;
  }
  struct timespec deadline;
  clock_gettime(CLOCK_MONOTONIC, &deadline);
  deadline.tv_nsec += hold_ns;
  if (deadline.tv_nsec >= 1000000000l) {
    deadline.tv_sec += 1;
    deadline.tv_nsec -= 1000000000l;
  }
  uint64_t wake = r.wake;
  const void * granted = NULL;
  for (bool waiting = hold_ns > 0;; ) {
    if (r.grant != 0 && r.grant < r.count && token_slot(tid) == TOKENS && token_slot(0) < TOKENS) {
      const void * handle = r.entries[r.grant].handle;
      for (size_t i = 0; i < slots && handle != NULL; ++i) {
        if (tls_gated[i] && tls_snapshot[i] == handle && *slot_of(wait_set, i) != NULL) {
          granted = handle;
        }
      }
    }
    if (granted != NULL || !waiting || !r.gating || r.wake != wake) {
      break;
    }
    waiting = pthread_cond_timedwait(&r_cond, &r_lock, &deadline) == 0;
  }
  if (!r.gating) {
    pthread_mutex_unlock(&r_lock);
    return ret;
  }
  for (size_t i = 0; i < slots; ++i) {
    if (tls_gated[i] && tls_snapshot[i] != granted) {
      *slot_of(wait_set, i) = NULL;
    }
  }
  if (granted != NULL) {
    int slot = token_slot(0);
    r.token_id[slot] = r.grant;
    r.token_tid[slot] = tid;
    r.entries[r.grant].ready = false;
    r.grant = 0;
  }
  pthread_mutex_unlock(&r_lock);
  return (granted != NULL || other) ? RCL_RET_OK : RCL_RET_TIMEOUT;
}

void asd_replay_publish(const void * publisher)
{
  pthread_mutex_lock(&r_lock);
  uint32_t id = find_id(publisher);
  if (id != 0) {
    send_line("pub\t%u\t%d", id, own_token() != 0);
  }
  pthread_mutex_unlock(&r_lock);
}

void asd_replay_take(const void * subscription)
{
  pthread_mutex_lock(&r_lock);
  uint32_t id = find_id(subscription);
  if (id != 0) {
    if (r.entries[id].pending > 0) {
      --r.entries[id].pending;
    }
    ++r.entries[id].takes;
    r.entries[id].ready = false;
    send_line("take\t%u\t%d", id, own_token() != 0);
  }
  pthread_mutex_unlock(&r_lock);
}

bool asd_replay_take_retry(const void * subscription, unsigned attempt)
{
  pthread_mutex_lock(&r_lock);
  uint32_t id = find_id(subscription);
  bool retry = r.gating && id != 0 && r.entries[id].pending > 0;
  if (retry && attempt >= TAKE_RETRIES) {
    r.entries[id].pending = 0;
    send_line("lost\t%u", id);
    retry = false;
  }
  pthread_mutex_unlock(&r_lock);
  if (retry) {
    usleep(TAKE_RETRY_US);
  }
  return retry;
}

void asd_replay_timer_called(const void * timer)
{
  pthread_mutex_lock(&r_lock);
  uint32_t id = find_id(timer);
  if (id != 0) {
    ++r.entries[id].takes;
    r.entries[id].ready = false;
    send_line("fire\t%u\t%d", id, own_token() != 0);
    report_next(id);
  }
  pthread_mutex_unlock(&r_lock);
}

void asd_replay_timer_changed(const void * timer)
{
  pthread_mutex_lock(&r_lock);
  uint32_t id = find_id(timer);
  if (id != 0) {
    report_next(id);
  }
  pthread_mutex_unlock(&r_lock);
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
  if (r.wall_free) {
    return clock;
  }
  pthread_mutex_lock(&r_clock_lock);
  if (!r.own_clock_ready) {
    rcl_allocator_t allocator = rcl_get_default_allocator();
    // rcl_clock_init is hooked: a ROS clock is driver-timed from its creation.
    r.own_clock_ready = rcl_clock_init(RCL_ROS_TIME, &r.own_clock, &allocator) == RCL_RET_OK;
  }
  pthread_mutex_unlock(&r_clock_lock);
  if (!r.own_clock_ready) {
    return clock;
  }
  *swapped = true;
  return &r.own_clock;
}

void asd_replay_clock_enabled(rcl_clock_t * clock)
{
  pthread_mutex_lock(&r_clock_lock);
  bool known = false;
  for (uint32_t i = 0; i < r.clock_count; ++i) {
    known = known || r.clocks[i] == clock;
  }
  if (!known) {
    if (r.clock_count == r.clock_capacity) {
      uint32_t capacity = r.clock_capacity != 0 ? 2 * r.clock_capacity : 64;
      rcl_clock_t ** clocks = realloc(r.clocks, capacity * sizeof(rcl_clock_t *));
      if (clocks == NULL) {
        pthread_mutex_unlock(&r_clock_lock);
        return;
      }
      r.clocks = clocks;
      r.clock_capacity = capacity;
    }
    r.clocks[r.clock_count++] = clock;
    if (r.time_set) {
      set_clock(clock, r.time_ns);
    }
  }
  pthread_mutex_unlock(&r_clock_lock);
}

void asd_replay_clock_gone(rcl_clock_t * clock)
{
  pthread_mutex_lock(&r_clock_lock);
  for (uint32_t i = 0; i < r.clock_count; ++i) {
    if (r.clocks[i] == clock) {
      r.clocks[i] = r.clocks[--r.clock_count];
      break;
    }
  }
  pthread_mutex_unlock(&r_clock_lock);
}

bool asd_replay_drops_time_override(void)
{
  return !tls_setting_time;
}

void asd_replay_clock_lock(void)
{
  pthread_mutex_lock(&r_clock_lock);
}

void asd_replay_clock_unlock(void)
{
  pthread_mutex_unlock(&r_clock_lock);
}
