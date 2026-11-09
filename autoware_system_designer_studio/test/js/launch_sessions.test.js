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
      rpc: async () => ({
        sessions: [
          {
            compute_unit: "main_ecu",
            state: "running",
            connected: true,
            member_count: 1,
            members: [{ path: "/a/x", state: "running", detail: { pid: 7 } }],
          },
        ],
      }),
    },
  },
};
require(PART);
const launch = global.window.SD.activities.launch;

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
  assert.equal(launch.sessions.get("main_ecu").member_count, 1);
  assert.equal(launch.members.get("/a/x").unit, "main_ecu");
  assert.deepEqual(emitted.at(-1), ["launch.sessions", launch.sessions]);

  handlers["daemon.member"]({
    compute_unit: "main_ecu",
    path: "/a/y",
    state: "running",
    detail: {},
    time: 1,
  });
  assert.equal(launch.members.get("/a/y").record.state, "running");
  assert.equal(launch.sessions.get("main_ecu").members.length, 2);
  assert.equal(emitted.at(-1)[0], "launch.member");

  handlers["daemon.launch"]({
    compute_unit: "main_ecu",
    state: "exited",
    connected: false,
    member_count: 2,
  });
  const session = launch.sessions.get("main_ecu");
  assert.equal(session.state, "exited");
  assert.equal(session.members.length, 2, "members survive a session view");
});

test("output rates label, mark busy logs and follow activity events", () => {
  assert.equal(launch.rateLabel(512), "512 B/s");
  assert.equal(launch.rateLabel(5120), "5.0 KB/s");
  assert.equal(launch.rateLabel(2 * 1048576), "2.0 MB/s");
  assert.equal(launch.busyLog(launch.BUSY_LOG_BPS - 1), false);
  assert.equal(launch.busyLog(launch.BUSY_LOG_BPS), true);

  handlers["daemon.launch"]({
    compute_unit: "main_ecu",
    state: "running",
    connected: true,
    member_count: 2,
  });
  handlers["daemon.member.activity"]({
    compute_unit: "main_ecu",
    window: 2,
    rates: { "/a/x": 8192, "/a/y": 0 },
  });
  assert.equal(launch.logRate("/a/x"), 8192);
  assert.equal(launch.logRate("/a/y"), 0);
  assert.deepEqual(emitted.at(-1), ["launch.activity", launch.activity]);
  handlers["daemon.member.activity"]({
    compute_unit: "sub_ecu",
    window: 2,
    rates: { "/b/z": 100 },
  });
  assert.equal(launch.logRate("/a/x"), 8192, "other units keep their rates");
  assert.equal(launch.logRate("/b/z"), 100);
  handlers["daemon.member.activity"]({
    compute_unit: "main_ecu",
    window: 2,
    rates: { "/a/y": 300 },
  });
  assert.equal(launch.logRate("/a/x"), 0, "a window replaces its unit's rates");
  assert.equal(launch.logRate("/a/y"), 300);

  handlers["daemon.launch"]({
    compute_unit: "main_ecu",
    state: "exited",
    connected: false,
    member_count: 2,
  });
  assert.equal(
    launch.logRate("/a/y"),
    0,
    "a lost channel ends its unit's rates",
  );
  assert.equal(launch.logRate("/b/z"), 100);
  handlers["daemon.member.activity"]({ compute_unit: "sub_ecu", rates: {} });
  assert.equal(launch.logRate("/b/z"), 0);
});
