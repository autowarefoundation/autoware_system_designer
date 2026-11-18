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
//     hello  protocol member pid hooks
//                               member: ASD_REPLAY_MEMBER, the member that started the process;
//                               hooks: ok, the rclcpp hooks that did not resolve
//                               (intra_process, execute), or - without rclcpp
//     node   fqn
//     ep     id kind(p|s) node topic reliability durability history depth
//                               the policies by their rmw names (rmw/qos_string_conversions.h)
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
//     pass   id on              on 1: the callbacks of the endpoint run without a grant; 0: gated again
//     match  seq                report the matched subscriptions of every publisher
//     free                      stop gating
//
// An id names one endpoint for the life of the process.

#include <errno.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "replay_state.h"

#define SOCKET_ENV "ASD_REPLAY_SOCKET"
#define START_ENV "ASD_REPLAY_START_NS"
#define MEMBER_ENV "ASD_REPLAY_MEMBER"
// "free" leaves timers of a steady or system clock on that clock, ungated.
#define WALL_TIMERS_ENV "ASD_REPLAY_WALL_TIMERS"
#define GATE_HOLD_ENV "ASD_REPLAY_GATE_HOLD_NS"
#define TAKE_TIMEOUT_ENV "ASD_REPLAY_TAKE_TIMEOUT_NS"
#define GRANTS_ENV "ASD_REPLAY_GRANTS"

#define DEFAULT_HOLD_NS 2000000ll
#define DEFAULT_TAKE_TIMEOUT_NS 2000000000ll
#define DEFAULT_GRANTS 4
#define MAX_GRANTS 64

replay_t asd_r = {.fd = -1};
static pthread_mutex_t r_state_lock = PTHREAD_MUTEX_INITIALIZER;
pthread_mutex_t asd_r_lock = PTHREAD_MUTEX_INITIALIZER;
pthread_cond_t asd_r_cond;
pthread_mutex_t asd_r_clock_lock;

// The condition variable waits on the monotonic clock; the clock lock is taken again by the hooks
// a time jump callback reaches.
__attribute__((constructor)) static void init_locks(void)
{
  pthread_condattr_t cond_attr;
  pthread_condattr_init(&cond_attr);
  pthread_condattr_setclock(&cond_attr, CLOCK_MONOTONIC);
  pthread_cond_init(&asd_r_cond, &cond_attr);
  pthread_condattr_destroy(&cond_attr);
  pthread_mutexattr_t mutex_attr;
  pthread_mutexattr_init(&mutex_attr);
  pthread_mutexattr_settype(&mutex_attr, PTHREAD_MUTEX_RECURSIVE);
  pthread_mutex_init(&asd_r_clock_lock, &mutex_attr);
  pthread_mutexattr_destroy(&mutex_attr);
}

// ---- socket ----------------------------------------------------------------------

void rp_stop_gating(void)
{
  __atomic_store_n(&asd_r.gating, false, __ATOMIC_RELAXED);
  pthread_cond_broadcast(&asd_r_cond);
}

void rp_send(const char * format, ...)
{
  if (asd_r.fd < 0) {
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
    ssize_t n = send(asd_r.fd, line + sent, (size_t)(length - sent), MSG_NOSIGNAL);
    if (n < 0) {
      if (errno == EINTR) {
        continue;
      }
      rp_stop_gating();
      return;
    }
    sent += (int)n;
  }
}

// ---- driver commands -----------------------------------------------------------------

static void on_command(char * line)
{
  unsigned long long a = 0;
  long long b = 0;
  if (sscanf(line, "time\t%llu\t%lld", &a, &b) == 2) {
    rp_on_time(a, b);
  } else if (sscanf(line, "match\t%llu", &a) == 1) {
    pthread_mutex_lock(&asd_r_lock);
    rp_on_match(a);
    pthread_mutex_unlock(&asd_r_lock);
  } else if (sscanf(line, "grant\t%llu", &a) == 1) {
    rp_on_grant((uint32_t)a);
  } else if (sscanf(line, "expect\t%llu", &a) == 1) {
    rp_on_expect((uint32_t)a);
  } else if (sscanf(line, "pass\t%llu\t%lld", &a, &b) == 2) {
    rp_on_pass((uint32_t)a, b != 0);
  } else if (strcmp(line, "free") == 0) {
    pthread_mutex_lock(&asd_r_lock);
    rp_stop_gating();
    pthread_mutex_unlock(&asd_r_lock);
  }
}

static void * reader_main(void * arg)
{
  (void)arg;
  char buffer[4096];
  size_t have = 0;
  for (;;) {
    ssize_t n = recv(asd_r.fd, buffer + have, sizeof(buffer) - have - 1, 0);
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
  pthread_mutex_lock(&asd_r_lock);
  rp_stop_gating();
  pthread_mutex_unlock(&asd_r_lock);
  return NULL;
}

// ---- arming ------------------------------------------------------------------------

// Value of a variable holding a whole number within [low, high], else `fallback`.
static long long env_number(const char * name, long long fallback, long long low, long long high)
{
  const char * text = getenv(name);
  if (text == NULL || text[0] == '\0') {
    return fallback;
  }
  char * end = NULL;
  long long value = strtoll(text, &end, 10);
  return (*end != '\0' || value < low || value > high) ? fallback : value;
}

static bool read_settings(void)
{
  const char * wall = getenv(WALL_TIMERS_ENV);
  asd_r.wall_free = wall != NULL && strcmp(wall, "free") == 0;
  asd_r.hold_ns = env_number(GATE_HOLD_ENV, DEFAULT_HOLD_NS, 0, 1000000000ll);
  long long take_ns = env_number(TAKE_TIMEOUT_ENV, DEFAULT_TAKE_TIMEOUT_NS, 0, 3600000000000ll);
  asd_r.take_retries = (uint32_t)(take_ns / (TAKE_RETRY_US * 1000ll));
  asd_r.tokens = (uint32_t)env_number(GRANTS_ENV, DEFAULT_GRANTS, 1, MAX_GRANTS);
  asd_r.token_id = calloc(asd_r.tokens, sizeof(uint32_t));
  asd_r.token_tid = calloc(asd_r.tokens, sizeof(uint32_t));
  const char * start = getenv(START_ENV);
  if (start != NULL && start[0] != '\0') {
    asd_r.time_ns = strtoll(start, NULL, 10);
    asd_r.time_set = true;
  }
  return asd_r.token_id != NULL && asd_r.token_tid != NULL;
}

static void say_hello(void)
{
  char member[256];
  const char * name = getenv(MEMBER_ENV);
  snprintf(member, sizeof(member), "%s", name != NULL ? name : "");
  for (char * c = member; *c != '\0'; ++c) {
    if (*c == '\t' || *c == '\n') {
      *c = ' ';
    }
  }
  char hooks[64];
  asd_replay_hook_state(hooks, sizeof(hooks));
  pthread_mutex_lock(&asd_r_lock);
  rp_send("hello\t%d\t%s\t%d\t%s", REPLAY_PROTOCOL, member, (int)getpid(), hooks);
  pthread_mutex_unlock(&asd_r_lock);
}

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
  if (!read_settings()) {
    close(fd);
    return STATE_OFF;
  }
  asd_r.fd = fd;
  __atomic_store_n(&asd_r.gating, true, __ATOMIC_RELAXED);
  say_hello();
  // The reader takes no signal: handlers stay on the process's own threads.
  sigset_t all, previous;
  sigfillset(&all);
  pthread_sigmask(SIG_SETMASK, &all, &previous);
  pthread_t thread;
  int created = pthread_create(&thread, NULL, reader_main, NULL);
  pthread_sigmask(SIG_SETMASK, &previous, NULL);
  if (created != 0) {
    close(fd);
    asd_r.fd = -1;
    __atomic_store_n(&asd_r.gating, false, __ATOMIC_RELAXED);
    return STATE_OFF;
  }
  pthread_detach(thread);
  return STATE_ON;
}

bool asd_replay_armed(void)
{
  int state = __atomic_load_n(&asd_r.state, __ATOMIC_ACQUIRE);
  if (state == STATE_UNSET) {
    pthread_mutex_lock(&r_state_lock);
    if (asd_r.state == STATE_UNSET) {
      __atomic_store_n(&asd_r.state, connect_locked(), __ATOMIC_RELEASE);
    }
    state = asd_r.state;
    pthread_mutex_unlock(&r_state_lock);
  }
  return state == STATE_ON;
}

// The link belongs to the parent; a child that reaches rcl connects for itself.
void asd_replay_at_fork_child(void)
{
  if (asd_r.state == STATE_ON) {
    close(asd_r.fd);
  }
  memset(&asd_r, 0, sizeof(asd_r));
  asd_r.fd = -1;
  pthread_mutex_init(&r_state_lock, NULL);
  pthread_mutex_init(&asd_r_lock, NULL);
  init_locks();
  rp_table_at_fork();
  rp_gate_at_fork();
}
