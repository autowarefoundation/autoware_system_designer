// Unit tests for the monitor's member tree: composable nodes under their
// container, and the aggregated run state of a group.

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
  "member_tree.js",
);

global.window = { SD: { activities: {} } };
require(PART);
const launch = global.window.SD.activities.launch;

function node(name, namespace, launchState, extra = {}) {
  const launcher = { launch_state: launchState, ...extra };
  return {
    entity_type: "node",
    name,
    namespace,
    path: `${namespace}/${name}`,
    launcher,
  };
}

test("the severest token wins", () => {
  assert.equal(launch.worstToken([]), "idle");
  assert.equal(launch.worstToken(["idle", "ok"]), "ok");
  assert.equal(launch.worstToken(["ok", "running", "idle"]), "running");
  assert.equal(launch.worstToken(["running", "warning", "ok"]), "warning");
  assert.equal(launch.worstToken(["warning", "failed"]), "failed");
});

test("token summary counts members severest first and leaves idle out", () => {
  assert.equal(
    launch.tokenSummary(["running", "idle", "running", "warning"]),
    "1 warning · 2 running",
  );
  assert.equal(launch.tokenSummary(["idle"]), "");
});

test("a composable follows its container once that left pending or running", () => {
  assert.equal(launch.followsContainer(undefined), false);
  assert.equal(launch.followsContainer({ state: "pending" }), false);
  assert.equal(launch.followsContainer({ state: "running" }), false);
  assert.equal(launch.followsContainer({ state: "stopped" }), true);
  assert.equal(launch.followsContainer({ state: "respawning" }), true);
  assert.equal(launch.followsContainer({ state: "failed" }), true);
  assert.equal(launch.followsContainer({ state: "done" }), true);
});

test("node ROS names strip a namespace segment equal to the name", () => {
  assert.equal(launch.nodeFqn({ name: "c", namespace: "/demo" }), "/demo/c");
  assert.equal(launch.nodeFqn({ name: "c", namespace: "/demo/c" }), "/demo/c");
  assert.equal(
    launch.nodeFqn({ name: "c", namespace: ["demo", "c"] }),
    "/demo/c",
  );
  assert.equal(launch.nodeFqn({ name: "c", namespace: "" }), "/c");
});

test("composable nodes nest under their container by path or ROS name", () => {
  const container = node("container", "/demo/group", "node_container");
  const byPath = node("a", "/demo", "composable_node", {
    container_target: "/demo/group/container",
  });
  const byFqn = node("b", "/demo", "composable_node", {
    container_target: "/demo/group/container",
  });
  const orphan = node("c", "/demo", "composable_node", {
    container_target: "/missing",
  });
  const single = node("d", "/demo", "single_node");
  const module = { entity_type: "module", name: "group", path: "/demo/group" };
  const nested = launch.nestComposables([
    module,
    container,
    byPath,
    byFqn,
    orphan,
    single,
  ]);
  const by = Object.fromEntries(nested.map((e) => [e.name, e]));
  assert.equal(by.a.path, "/demo/group/container/a");
  assert.equal(by.a.member_path, "/demo/a");
  assert.equal(by.a.container_path, "/demo/group/container");
  assert.equal(by.b.path, "/demo/group/container/b");
  assert.equal(by.c.path, "/demo/c");
  assert.equal(by.c.member_path, "/demo/c");
  assert.equal(by.c.container_path, undefined);
  assert.equal(by.d.member_path, "/demo/d");
  assert.equal(by.container.member_path, "/demo/group/container");
  assert.equal(by.group.path, "/demo/group");
  assert.equal(byPath.path, "/demo/a", "input entities are left as they are");
});

test("a container named by ROS name resolves when it differs from the path", () => {
  const container = node("pointcloud_container", "/", "node_container");
  container.path = "/sensing/pointcloud_container";
  const member = node("lidar", "/sensing", "composable_node", {
    container_target: "/pointcloud_container",
  });
  const [, nested] = launch.nestComposables([container, member]);
  assert.equal(nested.path, "/sensing/pointcloud_container/lidar");
  assert.equal(nested.container_path, "/sensing/pointcloud_container");
});

test("a node at a module's own path gets a leaf of its own under it", () => {
  const module = { entity_type: "module", name: "detection", path: "/detection" };
  const replay = node("detection_replay", "/", "single_node");
  replay.path = "/detection";
  const container = node("container", "/", "node_container");
  container.path = "/detection";
  const member = node("a", "/detection", "composable_node", {
    container_target: "/detection",
  });
  const nested = launch.nestComposables([module, replay, container, member]);
  const by = Object.fromEntries(nested.map((e) => [e.name, e]));
  assert.equal(by.detection.path, "/detection");
  assert.equal(by.detection_replay.path, "/detection/detection_replay");
  assert.equal(by.detection_replay.member_path, "/detection");
  assert.equal(by.container.path, "/detection/container");
  assert.equal(by.a.path, "/detection/container/a");
  assert.equal(by.a.container_path, "/detection");
});
