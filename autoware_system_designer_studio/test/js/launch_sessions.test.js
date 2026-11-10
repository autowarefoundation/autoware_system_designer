// Unit tests for the launch session store: member state mapping and the
// session and member labels the Launch activity shows.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const PART = path.join(
  __dirname,
  "..",
  "..",
  "autoware_system_designer_studio",
  "web",
  "js",
  "activities",
  "launch",
  "sessions.js",
);

// The daemon's answer to launch.status.
const SYS = "pkg:Sys";
const MODE = "default";

let statusAnswer = {
  sessions: [
    {
      compute_unit: "main_ecu",
      system: SYS,
      mode: MODE,
      state: "running",
      connected: true,
      member_count: 1,
      members: [{ path: "/a/x", state: "running", detail: { pid: 7 } }],
    },
  ],
};

const handlers = {};
const emitted = [];
global.window = {
  SD: {
    activities: {},
    on: (name, handler) => {
      handlers[name] = handler;
    },
    emit: (name, data) => emitted.push([name, data]),
    daemon: {
      offers: () => true,
      rpc: async () => statusAnswer,
    },
  },
};
require(PART);
const launch = global.window.SD.activities.launch;
const live = launch.scope(SYS, () => MODE);

test("member states map onto run-state tokens", () => {
  assert.equal(launch.stateToken(undefined), "idle");
  assert.equal(launch.stateToken({ state: "running" }), "running");
  assert.equal(launch.stateToken({ state: "loaded" }), "running");
  assert.equal(launch.stateToken({ state: "loading" }), "warning");
  assert.equal(launch.stateToken({ state: "respawning" }), "warning");
  assert.equal(launch.stateToken({ state: "failed" }), "failed");
  assert.equal(launch.stateToken({ state: "pending" }), "idle");
  assert.equal(launch.stateToken({ state: "done" }), "idle");
  assert.equal(
    launch.stateToken({ state: "stopped", detail: { exit_code: 0 } }),
    "ok",
  );
  assert.equal(
    launch.stateToken({ state: "stopped", detail: { exit_code: -15 } }),
    "warning",
  );
  assert.equal(launch.stateToken({ state: "future" }), "idle");
});

test("member labels carry the telling detail", () => {
  assert.equal(launch.stateLabel(undefined), "idle");
  assert.equal(
    launch.stateLabel({ state: "running", detail: { pid: 12 } }),
    "running · pid 12",
  );
  assert.equal(
    launch.stateLabel({ state: "stopped", detail: { exit_code: 1 } }),
    "stopped · exit 1",
  );
  assert.equal(
    launch.stateLabel({ state: "respawning", detail: { attempt: 2 } }),
    "respawning · attempt 2",
  );
  assert.equal(
    launch.stateLabel({ state: "failed", detail: { error: "boom" } }),
    "failed · boom",
  );
  assert.equal(launch.stateLabel({ state: "pending", detail: {} }), "pending");
});

test("session labels follow the session state", () => {
  assert.equal(launch.sessionLabel(null), "");
  assert.equal(launch.sessionLabel({ state: "starting" }), "starting");
  assert.equal(
    launch.sessionLabel({ state: "running", member_count: 3 }),
    "running · 3 members",
  );
  assert.equal(
    launch.sessionLabel({ state: "running", shutting_down: true }),
    "shutting down",
  );
  assert.equal(
    launch.sessionLabel({ state: "exited", job_state: "failed", exit_code: 1 }),
    "exited · code 1",
  );
  assert.equal(
    launch.sessionLabel({ state: "exited", job_state: "cancelled" }),
    "cancelled",
  );
});

test("session failures, their count and the session token", () => {
  const failed = (path, time, error) => ({
    path,
    state: "failed",
    detail: { error },
    time,
  });
  const session = {
    state: "exited",
    job_state: "failed",
    exit_code: 1,
    members: [
      { path: "/a", state: "done", detail: {}, time: 3 },
      failed("/b", 2, "load timed out"),
      failed("/c", 1, "spawn failed"),
    ],
  };
  assert.deepEqual(
    launch.sessionFailures(session).map((r) => r.path),
    ["/c", "/b"],
  );
  assert.deepEqual(launch.sessionFailures(null), []);
  assert.equal(launch.sessionLabel(session), "exited · code 1 · 2 failed");
  assert.equal(launch.sessionToken(session), "failed");
  assert.equal(
    launch.sessionLabel({ ...session, state: "running", member_count: 3 }),
    "running · 3 members · 2 failed",
  );
  assert.equal(launch.sessionToken(null), "idle");
  assert.equal(launch.sessionToken({ state: "running" }), "running");
  assert.equal(launch.sessionToken({ state: "starting" }), "warning");
  assert.equal(launch.sessionToken({ state: "stopping" }), "warning");
  assert.equal(launch.sessionToken({ state: "exited", exit_code: 0 }), "ok");
  assert.equal(
    launch.sessionToken({ state: "exited", exit_code: 1 }),
    "warning",
  );
  assert.equal(
    launch.sessionToken({ state: "exited", job_state: "cancelled" }),
    "idle",
  );
});

test("sessions resynchronise on hello and follow launch and member events", async () => {
  await handlers["daemon.health"]();
  assert.equal(live.sessions.get("main_ecu").member_count, 1);
  assert.equal(live.members.get("/a/x").unit, "main_ecu");
  assert.deepEqual(emitted.at(-1), ["launch.sessions", launch.sessions]);

  handlers["daemon.member"]({
    compute_unit: "main_ecu",
    system: SYS,
    mode: MODE,
    path: "/a/y",
    state: "running",
    detail: {},
    time: 1,
  });
  assert.equal(live.members.get("/a/y").record.state, "running");
  assert.equal(live.sessions.get("main_ecu").members.length, 2);
  assert.equal(emitted.at(-1)[0], "launch.member");

  handlers["daemon.launch"]({
    compute_unit: "main_ecu",
    system: SYS,
    mode: MODE,
    state: "exited",
    connected: false,
    member_count: 2,
  });
  const session = live.sessions.get("main_ecu");
  assert.equal(session.state, "exited");
  assert.equal(session.members.length, 2, "members survive a session view");
});

test("a mode keeps its last session beside another mode's on the unit", () => {
  const other = launch.scope(SYS, () => "other");
  assert.equal(other.sessions.get("main_ecu"), undefined);
  handlers["daemon.launch"]({
    compute_unit: "main_ecu",
    system: SYS,
    mode: "other",
    job: "job-other",
    state: "running",
    connected: false,
    member_count: 0,
  });
  handlers["daemon.member"]({
    compute_unit: "main_ecu",
    system: SYS,
    mode: "other",
    path: "/a/x",
    state: "failed",
    detail: {},
    time: 2,
  });
  assert.equal(other.members.get("/a/x").record.state, "failed");
  assert.equal(live.sessions.get("main_ecu").state, "exited");
  assert.notEqual(live.members.get("/a/x").record.state, "failed");
  // A new launch of the mode starts without the members of its last.
  handlers["daemon.launch"]({
    compute_unit: "main_ecu",
    system: SYS,
    mode: "other",
    job: "job-other-2",
    state: "starting",
    connected: false,
    member_count: 0,
  });
  assert.equal(other.members.get("/a/x"), undefined);
  assert.equal(other.sessions.get("main_ecu").members.length, 0);
});

test("output rates label, mark busy logs and follow activity events", () => {
  assert.equal(launch.rateLabel(512), "512 B/s");
  assert.equal(launch.rateLabel(5120), "5.0 KB/s");
  assert.equal(launch.rateLabel(2 * 1048576), "2.0 MB/s");
  assert.equal(launch.busyLog(launch.BUSY_LOG_BPS - 1), false);
  assert.equal(launch.busyLog(launch.BUSY_LOG_BPS), true);

  handlers["daemon.launch"]({
    compute_unit: "main_ecu",
    system: SYS,
    mode: MODE,
    state: "running",
    connected: true,
    member_count: 2,
  });
  handlers["daemon.member.activity"]({
    compute_unit: "main_ecu",
    system: SYS,
    mode: MODE,
    window: 2,
    rates: { "/a/x": 8192, "/a/y": 0 },
  });
  assert.equal(live.logRate("/a/x"), 8192);
  assert.equal(live.logRate("/a/y"), 0);
  assert.equal(emitted.at(-1)[0], "launch.activity");
  handlers["daemon.launch"]({
    compute_unit: "sub_ecu",
    system: SYS,
    mode: MODE,
    state: "running",
    connected: true,
    member_count: 1,
  });
  handlers["daemon.member.activity"]({
    compute_unit: "sub_ecu",
    system: SYS,
    mode: MODE,
    window: 2,
    rates: { "/b/z": 100 },
  });
  assert.equal(live.logRate("/a/x"), 8192, "other units keep their rates");
  assert.equal(live.logRate("/b/z"), 100);
  handlers["daemon.member.activity"]({
    compute_unit: "main_ecu",
    system: SYS,
    mode: MODE,
    window: 2,
    rates: { "/a/y": 300 },
  });
  assert.equal(live.logRate("/a/x"), 0, "a window replaces its unit's rates");
  assert.equal(live.logRate("/a/y"), 300);

  handlers["daemon.launch"]({
    compute_unit: "main_ecu",
    system: SYS,
    mode: MODE,
    state: "exited",
    connected: false,
    member_count: 2,
  });
  assert.equal(live.logRate("/a/y"), 0, "a lost channel ends its unit's rates");
  assert.equal(live.logRate("/b/z"), 100);
  handlers["daemon.member.activity"]({
    compute_unit: "sub_ecu",
    system: SYS,
    mode: MODE,
    rates: {},
  });
  assert.equal(live.logRate("/b/z"), 0);
});

test("a run is labelled by its start time and what it ran", () => {
  const label = launch.runLabel({
    run: "20261109_124314",
    started: new Date(2026, 10, 9, 12, 43, 14).getTime() / 1000,
    system: "AutowareSample",
    mode: "LoggingSimulation",
    deploy: "site_a",
    compute_unit: "main_ecu",
  });
  assert.equal(
    label,
    "2026-11-09 12:43:14 · LoggingSimulation · site_a · main_ecu",
  );
  assert.equal(launch.runLabel({ run: "custom", started: 0 }), "custom");
  assert.equal(
    launch.runLabel({ run: "x", started: 0, compute_unit: "sub_ecu" }),
    "x · sub_ecu",
  );
});

test("a status answer older than a received member event keeps the event's record", async () => {
  statusAnswer = {
    sessions: [
      {
        compute_unit: "main_ecu",
        system: SYS,
        mode: MODE,
        job: "job-9",
        state: "running",
        connected: true,
        member_count: 2,
        members: [
          { path: "/a/x", state: "pending", detail: {}, time: 10 },
          {
            path: "/a/y",
            state: "stopped",
            detail: { exit_code: 1 },
            time: 10,
          },
        ],
      },
    ],
  };
  await handlers["daemon.health"]();
  handlers["daemon.member"]({
    compute_unit: "main_ecu",
    system: SYS,
    mode: MODE,
    path: "/a/x",
    state: "running",
    detail: {},
    time: 20,
  });
  await handlers["daemon.health"]();
  assert.equal(live.members.get("/a/x").record.state, "running");
  assert.equal(live.members.get("/a/y").record.state, "stopped");
  const records = live.sessions.get("main_ecu").members;
  assert.equal(records.find((r) => r.path === "/a/x").state, "running");
  assert.equal(live.restartsOf("/a/x"), null);
});
