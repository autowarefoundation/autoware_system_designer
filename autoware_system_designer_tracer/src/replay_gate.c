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

// The gate of a process: its subscription and timer callbacks run one at a time, each on a
// grant of the replay driver.
//
// An executor learns of work through rcl_wait, so the gate sits there: the hook
// reports which subscriptions and timers are ready, hides all of them from the
// executor, and hands back the one the driver grants. The thread that received a
// grant holds it until its callback is over: the return of rclcpp's
// Executor::execute_any_executable, else its next rcl_wait. Each thread holds one
// grant at most, `tokens` threads at a time.

#include <stdlib.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#include "replay_state.h"

static __thread uint32_t tls_tid = 0;
// Depth of rclcpp executor callbacks on this thread; a wait inside one is part of the callback.
static __thread int tls_callback_depth = 0;
static __thread const void ** tls_snapshot = NULL;
static __thread bool * tls_gated = NULL;
// Takes of each slot's entry when the wait began; a take during the wait voids what it saw.
static __thread uint32_t * tls_takes = NULL;
static __thread size_t tls_snapshot_size = 0;

static uint32_t self_tid(void)
{
  if (tls_tid == 0) {
    tls_tid = (uint32_t)syscall(SYS_gettid);
  }
  return tls_tid;
}

void rp_gate_at_fork(void)
{
  tls_tid = 0;
  tls_callback_depth = 0;
}

// ---- grants ----------------------------------------------------------------------------

// Slot of the grant `tid` holds, `tokens` when it holds none; tid 0 finds a free slot.
static uint32_t token_slot(uint32_t tid)
{
  uint32_t slot = 0;
  while (slot < asd_r.tokens && asd_r.token_tid[slot] != tid) {
    ++slot;
  }
  return slot;
}

uint32_t rp_own_token(void)
{
  uint32_t slot = token_slot(self_tid());
  return slot < asd_r.tokens ? asd_r.token_id[slot] : 0;
}

// Ends the grant `tid` holds: its callback is over. Called with asd_r_lock held.
static void release_grant(uint32_t tid)
{
  uint32_t slot = token_slot(tid);
  if (slot == asd_r.tokens) {
    return;
  }
  rp_send("done\t%u", asd_r.token_id[slot]);
  asd_r.token_tid[slot] = 0;
  asd_r.token_id[slot] = 0;
  ++asd_r.wake;
  pthread_cond_broadcast(&asd_r_cond);
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
  pthread_mutex_lock(&asd_r_lock);
  release_grant(self_tid());
  pthread_mutex_unlock(&asd_r_lock);
}

// ---- driver commands -------------------------------------------------------------------

void rp_on_grant(uint32_t id)
{
  pthread_mutex_lock(&asd_r_lock);
  asd_r.grant = id;
  pthread_cond_broadcast(&asd_r_cond);
  pthread_mutex_unlock(&asd_r_lock);
}

void rp_on_expect(uint32_t id)
{
  pthread_mutex_lock(&asd_r_lock);
  entry_t * e = rp_entry(id);
  if (e != NULL && (e->depth == 0 || e->pending < e->depth)) {
    ++e->pending;
  }
  pthread_mutex_unlock(&asd_r_lock);
}

void rp_on_pass(uint32_t id, bool on)
{
  pthread_mutex_lock(&asd_r_lock);
  entry_t * e = rp_entry(id);
  if (e != NULL) {
    e->pass = on;
    e->ready = false;
    ++asd_r.wake;
    pthread_cond_broadcast(&asd_r_cond);
  }
  pthread_mutex_unlock(&asd_r_lock);
}

// ---- wait ------------------------------------------------------------------------------

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

// The handle of the driver's grant when this thread may take it: the endpoint is ready in this
// wait set, the thread holds no grant and a token is free. Called with asd_r_lock held.
static const void * grant_for(rcl_wait_set_t * wait_set, size_t slots, uint32_t tid)
{
  if (asd_r.grant == 0 || token_slot(tid) != asd_r.tokens || token_slot(0) == asd_r.tokens) {
    return NULL;
  }
  const entry_t * e = rp_entry(asd_r.grant);
  if (e == NULL) {
    return NULL;
  }
  for (size_t i = 0; i < slots; ++i) {
    if (tls_gated[i] && tls_snapshot[i] == e->handle && *slot_of(wait_set, i) != NULL) {
      return e->handle;
    }
  }
  return NULL;
}

rcl_ret_t asd_replay_wait(asd_wait_fn real, rcl_wait_set_t * wait_set, int64_t timeout)
{
  if (wait_set == NULL || !__atomic_load_n(&asd_r.gating, __ATOMIC_RELAXED)) {
    return real(wait_set, timeout);
  }
  uint32_t tid = self_tid();
  size_t slots = wait_set->size_of_subscriptions + wait_set->size_of_timers;
  if (!reserve_snapshot(slots)) {
    return real(wait_set, timeout);
  }
  pthread_mutex_lock(&asd_r_lock);
  if (tls_callback_depth == 0) {
    release_grant(tid);
  }
  for (size_t i = 0; i < slots; ++i) {
    tls_snapshot[i] = *slot_of(wait_set, i);
    entry_t * e = rp_find(tls_snapshot[i]);
    tls_takes[i] = e != NULL ? e->takes : 0;
    if (e != NULL && e->gated && !e->waited) {
      e->waited = true;
      rp_send("waited\t%u", e->id);
    }
  }
  pthread_mutex_unlock(&asd_r_lock);

  rcl_ret_t ret = real(wait_set, timeout);
  if (ret != RCL_RET_OK && ret != RCL_RET_TIMEOUT) {
    return ret;
  }

  pthread_mutex_lock(&asd_r_lock);
  if (!asd_r.gating) {
    pthread_mutex_unlock(&asd_r_lock);
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
    entry_t * e = rp_find(tls_snapshot[i]);
    if (e == NULL || !e->gated || e->pass) {
      other = other || ready;
      continue;
    }
    tls_gated[i] = true;
    if (ready && e->takes != tls_takes[i]) {
      *slot_of(wait_set, i) = NULL;
      stale = true;
      continue;
    }
    if (ready != e->ready) {
      e->ready = ready;
      rp_send(ready ? "ready\t%u" : "unready\t%u", e->id);
      if (!ready && asd_r.grant == e->id) {
        asd_r.grant = 0;
      }
    }
    held += ready;
  }
  if (held == 0) {
    pthread_mutex_unlock(&asd_r_lock);
    return ret;
  }

  int64_t hold_ns = (other || stale || timeout == 0) ? 0 : asd_r.hold_ns;
  if (timeout > 0 && timeout < hold_ns) {
    hold_ns = timeout;
  }
  struct timespec deadline;
  clock_gettime(CLOCK_MONOTONIC, &deadline);
  deadline.tv_nsec += hold_ns % 1000000000ll;
  deadline.tv_sec += hold_ns / 1000000000ll;
  if (deadline.tv_nsec >= 1000000000l) {
    deadline.tv_sec += 1;
    deadline.tv_nsec -= 1000000000l;
  }
  uint64_t wake = asd_r.wake;
  const void * granted = NULL;
  for (bool waiting = hold_ns > 0;; ) {
    granted = grant_for(wait_set, slots, tid);
    if (granted != NULL || !waiting || !asd_r.gating || asd_r.wake != wake) {
      break;
    }
    waiting = pthread_cond_timedwait(&asd_r_cond, &asd_r_lock, &deadline) == 0;
  }
  if (!asd_r.gating) {
    pthread_mutex_unlock(&asd_r_lock);
    return ret;
  }
  for (size_t i = 0; i < slots; ++i) {
    if (tls_gated[i] && tls_snapshot[i] != granted) {
      *slot_of(wait_set, i) = NULL;
    }
  }
  if (granted != NULL) {
    uint32_t slot = token_slot(0);
    asd_r.token_id[slot] = asd_r.grant;
    asd_r.token_tid[slot] = tid;
    rp_entry(asd_r.grant)->ready = false;
    asd_r.grant = 0;
  }
  pthread_mutex_unlock(&asd_r_lock);
  return (granted != NULL || other) ? RCL_RET_OK : RCL_RET_TIMEOUT;
}

// ---- publishes, takes and timer calls ----------------------------------------------------

void asd_replay_publish(const void * publisher)
{
  pthread_mutex_lock(&asd_r_lock);
  const entry_t * e = rp_find(publisher);
  if (e != NULL) {
    rp_send("pub\t%u\t%d", e->id, rp_own_token() != 0);
  }
  pthread_mutex_unlock(&asd_r_lock);
}

void asd_replay_take(const void * subscription)
{
  pthread_mutex_lock(&asd_r_lock);
  entry_t * e = rp_find(subscription);
  if (e != NULL) {
    if (e->pending > 0) {
      --e->pending;
    }
    ++e->takes;
    e->ready = false;
    rp_send("take\t%u\t%d", e->id, rp_own_token() != 0);
  }
  pthread_mutex_unlock(&asd_r_lock);
}

bool asd_replay_take_retry(const void * subscription, unsigned attempt)
{
  pthread_mutex_lock(&asd_r_lock);
  entry_t * e = rp_find(subscription);
  bool retry = asd_r.gating && e != NULL && e->pending > 0;
  if (retry && attempt >= asd_r.take_retries) {
    e->pending = 0;
    rp_send("lost\t%u", e->id);
    retry = false;
  }
  pthread_mutex_unlock(&asd_r_lock);
  if (retry) {
    usleep(TAKE_RETRY_US);
  }
  return retry;
}

void asd_replay_timer_called(const void * timer)
{
  pthread_mutex_lock(&asd_r_lock);
  entry_t * e = rp_find(timer);
  if (e != NULL) {
    ++e->takes;
    e->ready = false;
    rp_send("fire\t%u\t%d", e->id, rp_own_token() != 0);
    rp_report_next(e);
  }
  pthread_mutex_unlock(&asd_r_lock);
}
