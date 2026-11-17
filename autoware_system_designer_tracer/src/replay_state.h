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

// State of the replay gate shared by its parts: the link to the driver (replay_link.c), the
// endpoint table (replay_table.c), the clocks (replay_clock.c) and the gate (replay_gate.c).

#ifndef REPLAY_STATE_H_
#define REPLAY_STATE_H_

#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>

#include <rcl/time.h>

#include "replay.h"

#define ASD_LOCAL __attribute__((visibility("hidden")))

// Version of the lines both sides speak; PROTOCOL of the driver's link.py.
#define REPLAY_PROTOCOL 2

enum { STATE_UNSET = 0, STATE_OFF = 1, STATE_ON = 2 };
enum { KIND_PUBLISHER = 0, KIND_SUBSCRIPTION = 1, KIND_TIMER = 2, KIND_COUNT = 3 };

// A live publisher, subscription or timer of the process.
typedef struct entry
{
  uint32_t id;          // never reused within the process
  const void * handle;
  char kind;            // 'p', 's', 't'
  bool gated;
  bool pass;            // gated endpoint whose callbacks run without a grant
  bool waited;
  bool ready;
  uint32_t depth;       // subscription: keep-last depth, 0 without a bound
  uint32_t pending;     // subscription: messages published to it and not taken
  uint32_t takes;       // messages taken, or calls of the timer
  char * node;
  rcl_clock_t * clock;  // timer
  int64_t next_ns;      // timer: the next call time last reported
  // Neighbours among the live entries of its kind.
  struct entry * next;
  struct entry * prev;
} entry_t;

typedef struct replay
{
  int state;
  int fd;
  bool gating;
  bool wall_free;
  // Longest stay in rcl_wait with ready work and no grant; bounds how late the executor sees new work.
  int64_t hold_ns;
  // Tries of a take with a message in flight, TAKE_RETRY_US apart.
  uint32_t take_retries;
  // The grant the driver issued, and the threads running the grants taken: `tokens` at a time.
  uint32_t grant;
  uint32_t tokens;
  uint32_t * token_id;
  uint32_t * token_tid;
  uint64_t wake;
  rcl_clock_t ** clocks;
  uint32_t clock_count;
  uint32_t clock_capacity;
  bool time_set;
  int64_t time_ns;
  rcl_clock_t own_clock;
  bool own_clock_ready;
} replay_t;

#define TAKE_RETRY_US 200

ASD_LOCAL extern replay_t asd_r;
// Entries, grant and socket writes.
ASD_LOCAL extern pthread_mutex_t asd_r_lock;
ASD_LOCAL extern pthread_cond_t asd_r_cond;
// Clock list and every change of a clock's override or jump callbacks; taken before asd_r_lock.
ASD_LOCAL extern pthread_mutex_t asd_r_clock_lock;

// ---- link: called with asd_r_lock held ---------------------------------------------
// One line to the driver. A failed write ends the gating: the process runs free.
ASD_LOCAL void rp_send(const char * format, ...) __attribute__((format(printf, 1, 2)));
ASD_LOCAL void rp_stop_gating(void);

// ---- table: called with asd_r_lock held --------------------------------------------
ASD_LOCAL entry_t * rp_find(const void * handle);
ASD_LOCAL entry_t * rp_entry(uint32_t id);
// First live entry of a kind; the rest follow by `next`.
ASD_LOCAL entry_t * rp_live(int kind);
ASD_LOCAL void rp_on_match(unsigned long long seq);
ASD_LOCAL void rp_table_at_fork(void);

// ---- clock ---------------------------------------------------------------------------
// Next call time of a timer on its own clock; -1 when it is canceled.
ASD_LOCAL int64_t rp_timer_next(const entry_t * e);
// Reports a timer's next call time when it changed. Called with asd_r_lock held.
ASD_LOCAL void rp_report_next(entry_t * e);
ASD_LOCAL void rp_on_time(unsigned long long seq, long long ns);

// ---- gate ----------------------------------------------------------------------------
// Id of the endpoint whose grant the calling thread holds, else 0. Called with asd_r_lock held.
ASD_LOCAL uint32_t rp_own_token(void);
ASD_LOCAL void rp_on_grant(uint32_t id);
ASD_LOCAL void rp_on_expect(uint32_t id);
ASD_LOCAL void rp_on_pass(uint32_t id, bool on);
ASD_LOCAL void rp_gate_at_fork(void);

#endif  // REPLAY_STATE_H_
