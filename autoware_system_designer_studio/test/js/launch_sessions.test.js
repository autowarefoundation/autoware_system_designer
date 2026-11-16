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

test("the runs of a launch are gathered where its first listed run stands", () => {
  const at = (s) => new Date(2026, 10, 9, 12, 0, s).getTime() / 1000;
  const run = (name, started, compute_unit, launchId) => ({
    run: name,
    started: at(started),
    mode: "Runtime",
    compute_unit,
    launch: launchId,
  });
  const groups = launch.runGroups([
    run("sub/c", 40, "sub_ecu", "A"),
    run("d", 30, "main_ecu", undefined),
    run("sub/b", 2, "sub_ecu", "A"),
    run("a", 1, "main_ecu", "A"),
  ]);
  assert.deepEqual(
    groups.map((g) => [g.launch, g.runs.map((r) => r.run)]),
    [
      ["A", ["sub/c", "sub/b", "a"]],
      [null, ["d"]],
    ],
  );
  assert.deepEqual(
    groups.map((g) => [g.value, g.label]),
    [
      ["launch:A", "2026-11-09 12:00:01 · Runtime · main_ecu, sub_ecu"],
      ["d", "2026-11-09 12:00:30 · Runtime · main_ecu"],
    ],
  );
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

test("the runs read answer in order, a failed one leaving its message", async () => {
  const daemon = global.window.SD.daemon;
  const rpc = daemon.rpc;
  const warn = console.warn;
  console.warn = () => {};
  daemon.rpc = async (method, { run }) => {
    if (run === "b") throw new Error("no run 'b'");
    return { method, run };
  };
  try {
    const answers = await launch.readRuns(
      [{ run: "a" }, { run: "b" }, { run: "c" }],
      "launch.run",
      (r) => ({ run: r.run }),
    );
    assert.deepEqual(
      answers.read.map(({ run, result }) => [run.run, result.run]),
      [
        ["a", "a"],
        ["c", "c"],
      ],
    );
    assert.equal(answers.error, "no run 'b'");
  } finally {
    daemon.rpc = rpc;
    console.warn = warn;
  }
});

test("the run selector lists launches and shows Live once the shown one leaves", async () => {
  const SD = global.window.SD;
  const rpc = SD.daemon.rpc;
  const widgets = SD.widgets;
  SD.widgets = {
    element: (tag, _className, text) => ({
      tag,
      text,
      value: "",
      hidden: false,
      options: [],
      addEventListener() {},
      replaceChildren(...children) {
        this.options = children;
      },
    }),
  };
  let listed = [
    {
      run: "a",
      started: 2,
      compute_unit: "main_ecu",
      launch: "A",
      rates: true,
    },
    {
      run: "sub/a",
      started: 3,
      compute_unit: "sub_ecu",
      launch: "A",
      rates: true,
    },
    {
      run: "b",
      started: 1,
      compute_unit: "main_ecu",
      launch: "B",
      rates: false,
    },
  ];
  SD.daemon.rpc = async () => ({ runs: listed });
  const changes = [];
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  try {
    const selector = launch.runSelector({
      systemId: SYS,
      offered: "launch.run_rates",
      filter: (r) => r.rates,
      onChange: (value) => changes.push(value),
    });
    selector.load();
    await settle();
    assert.deepEqual(
      selector.element.options.map((o) => o.value),
      ["", "launch:A"],
    );
    assert.deepEqual(
      selector.pick("launch:A").runs.map((r) => r.run),
      ["a", "sub/a"],
    );
    assert.equal(selector.pick("launch:B"), null);
    assert.equal(selector.element.value, "");
    selector.pick("launch:A");
    listed = listed.slice(2);
    selector.load();
    await settle();
    assert.deepEqual(changes, [""]);
    assert.equal(selector.element.value, "");
  } finally {
    SD.daemon.rpc = rpc;
    SD.widgets = widgets;
  }
});

test("a launch over every unit of its mode is labelled <all units>", () => {
  const unitsOf = launch.modeUnits([
    { mode: "Runtime", unit: "main_ecu", deploy: "" },
    { mode: "Runtime", unit: "sub_ecu", deploy: "" },
    { mode: "Runtime", unit: "sub_ecu", deploy: "site_a" },
    { mode: "Other", unit: "x_ecu", deploy: "" },
  ]);
  assert.deepEqual(unitsOf("Runtime", null), ["main_ecu", "sub_ecu"]);
  assert.deepEqual(unitsOf("Runtime", "site_a"), ["sub_ecu"]);
  const run = (name, unit, launchId) => ({
    run: name,
    started: 1,
    mode: "Runtime",
    compute_unit: unit,
    launch: launchId,
  });
  const labels = launch
    .runGroups(
      [
        run("a", "main_ecu", "A"),
        run("sub/a", "sub_ecu", "A"),
        run("b", "main_ecu", "B"),
      ],
      unitsOf,
    )
    .map((g) => g.label.split(" · ").pop());
  assert.deepEqual(labels, ["<all units>", "main_ecu"]);
  assert.equal(launch.unitsLabel(["sub_ecu"], ["sub_ecu"]), "sub_ecu");
  assert.equal(launch.unitsLabel(["b", "a", "a"]), "a, b");
});
