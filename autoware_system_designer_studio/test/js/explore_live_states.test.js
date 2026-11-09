// Unit tests for the live state the diagrams overlay: member states keyed by
// node path with composables following their container, restart counts kept
// per launch, and the live cost wrapper that kills a dead member's gates.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const JS_DIR = path.join(
  __dirname,
  "..",
  "..",
  "autoware_system_designer_studio",
  "web",
  "js",
);

const handlers = {};
global.window = {
  SD: {
    activities: {},
    on: (name, handler) => {
      handlers[name] = handler;
    },
    emit: () => {},
    daemon: { offers: () => false, rpc: async () => ({ sessions: [] }) },
  },
};
require(path.join(JS_DIR, "widgets", "namespace_tree.js"));
require(path.join(JS_DIR, "activities", "launch", "sessions.js"));
require(path.join(JS_DIR, "activities", "launch", "member_tree.js"));
const T = require(path.join(JS_DIR, "diagrams", "timing_model.js"));
const launch = global.window.SD.activities.launch;

function node(name, namespace, launchState, extra = {}) {
  return {
    entity_type: "node",
    name,
    namespace,
    path: `${namespace}/${name}`,
    unique_id: `${namespace}/${name}`,
    launcher: { launch_state: launchState, ...extra },
  };
}

const ENTITIES = launch.nestComposables([
  node("alpha", "/a", "single_node"),
  node("container", "/a", "node_container"),
  node("comp", "/a", "composable_node", { container_target: "/a/container" }),
  node("beta", "/b", "single_node"),
]);

function session(unit, system, job = "job-1") {
  handlers["daemon.launch"]({
    compute_unit: unit,
    system,
    job,
    state: "running",
    connected: true,
    member_count: 0,
  });
}

function member(unit, memberPath, state, detail = {}) {
  handlers["daemon.member"]({
    compute_unit: unit,
    system: "pkg:Sys",
    path: memberPath,
    state,
    detail,
    time: 1,
  });
}

test("member states are keyed by node path for the system's own sessions", () => {
  session("main_ecu", "pkg:Sys");
  session("other", "pkg:Other");
  member("main_ecu", "/a/alpha", "running", { pid: 3 });
  member("main_ecu", "/a/container", "running", { pid: 4 });
  member("main_ecu", "/a/comp", "loaded", { node: "comp" });
  member("other", "/b/beta", "running", { pid: 9 });

  const states = launch.memberStates(ENTITIES, "pkg:Sys");
  assert.deepEqual([...states.keys()].sort(), [
    "/a/alpha",
    "/a/comp",
    "/a/container",
  ]);
  assert.equal(states.get("/a/alpha").token, "running");
  assert.equal(states.get("/a/alpha").dead, null);
  assert.equal(states.get("/a/comp").token, "running");
});

test("a stopped member is dead and a composable follows its container down", () => {
  member("main_ecu", "/a/alpha", "stopped", { exit_code: 1 });
  member("main_ecu", "/a/container", "stopped", { exit_code: 0 });

  const states = launch.memberStates(ENTITIES, "pkg:Sys");
  assert.equal(states.get("/a/alpha").token, "warning");
  assert.equal(states.get("/a/alpha").dead, "stopped · exit 1");
  assert.equal(states.get("/a/container").token, "ok");
  assert.equal(states.get("/a/container").dead, "stopped · exit 0");
  assert.equal(states.get("/a/comp").token, "ok");
  assert.match(states.get("/a/comp").dead, /container stopped · exit 0/);
});

test("restarts count re-entries into running, respawns among them", () => {
  member("main_ecu", "/a/alpha", "running", { pid: 5 });
  assert.deepEqual(launch.restartsOf("/a/alpha"), {
    unit: "main_ecu",
    restarts: 1,
    respawns: 0,
  });
  member("main_ecu", "/a/alpha", "stopped", { exit_code: 2 });
  member("main_ecu", "/a/alpha", "respawning", { attempt: 1 });
  member("main_ecu", "/a/alpha", "running", { pid: 6 });
  member("main_ecu", "/a/alpha", "respawning", { attempt: 2 });
  member("main_ecu", "/a/alpha", "running", { pid: 7 });
  assert.deepEqual(launch.restartsOf("/a/alpha"), {
    unit: "main_ecu",
    restarts: 3,
    respawns: 2,
  });
  assert.equal(
    launch.memberStates(ENTITIES, "pkg:Sys").get("/a/alpha").restarts.restarts,
    3,
  );
  assert.equal(launch.restartsOf("/b/beta"), null);
});

test("a new launch of the unit resets its counters", () => {
  session("main_ecu", "pkg:Sys", "job-2");
  assert.equal(launch.restartsOf("/a/alpha"), null);
  member("main_ecu", "/a/alpha", "running", { pid: 8 });
  assert.equal(launch.restartsOf("/a/alpha"), null);
});

test("the overlay switch off leaves no state for the diagrams", () => {
  assert.equal(launch.memberStates([], "pkg:Sys").size, 0);
});

test("live costs kill the gates of dead members and leave the rest to the base", () => {
  const base = {
    wait: () => T.summary({ min: 1, mean: 2, max: 3 }),
    exec: () => T.summary({ min: 4, mean: 5, max: 6 }),
    comm: () => T.summary({ min: 7, mean: 8, max: 9 }),
  };
  const costs = T.liveCosts(base, (event) =>
    event.id === "dead" ? "stopped · exit 1" : null,
  );
  assert.equal(costs.exec({ id: "dead" }).dead, true);
  assert.equal(costs.exec({ id: "dead" }).reason, "stopped · exit 1");
  assert.equal(costs.wait({ id: "dead" }).max, 0);
  assert.equal(costs.exec({ id: "live" }).mean, 5);
  assert.equal(costs.wait({ id: "live" }).mean, 2);
  assert.equal(costs.comm({}, { id: "x" }, { id: "y" }).mean, 8);
  // An or gate folds the live branch; an and gate waiting on the dead one is dead.
  const branches = [
    { key: "a", summary: costs.exec({ id: "dead" }) },
    { key: "b", summary: costs.exec({ id: "live" }) },
  ];
  assert.equal(T.fold(branches, "min").summary.dead, false);
  assert.equal(T.fold(branches, "max").summary.dead, true);
});
