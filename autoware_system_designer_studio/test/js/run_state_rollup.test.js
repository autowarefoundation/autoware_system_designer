// Unit tests for the run-state roll-up over a design tree: a node's own state,
// a module's severest token with member counts summed through nested modules,
// and the dead flag when every member within is down.

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

global.window = { SD: {} };
require(path.join(JS_DIR, "widgets", "run_state.js"));
const { summarize, rollup } = global.window.SD.RunState;

const ROOT = {
  unique_id: "root",
  entity_type: "system",
  children: [
    {
      unique_id: "sensing",
      entity_type: "module",
      children: [
        {
          unique_id: "lidar",
          entity_type: "module",
          children: [
            { unique_id: "a", entity_type: "node", path: "/s/l/a" },
            { unique_id: "b", entity_type: "node", path: "/s/l/b" },
            { unique_id: "c", entity_type: "node", path: "/s/l/c" },
          ],
        },
        { unique_id: "cam", entity_type: "node", path: "/s/cam" },
      ],
    },
    { unique_id: "idle", entity_type: "node", path: "/idle" },
  ],
};

function state(token, dead = null) {
  return { token, label: token, dead, restarts: null };
}

test("rollup keys every instance with a launched member within", () => {
  const states = new Map([
    ["/s/l/a", state("running")],
    ["/s/l/b", state("failed", "failed")],
    ["/s/l/c", state("warning", "respawning")],
    ["/s/cam", state("running")],
  ]);
  const byId = rollup(ROOT, states);
  assert.deepEqual(Array.from(byId.keys()).sort(), [
    "a",
    "b",
    "c",
    "cam",
    "lidar",
    "root",
    "sensing",
  ]);
  assert.equal(byId.get("a").members, 1);
  const lidar = byId.get("lidar");
  assert.equal(lidar.token, "failed");
  assert.equal(lidar.label, "1 failed · 1 warning · 1 running");
  assert.equal(lidar.dead, null);
  // Counts of a nested module are summed per token, not under its own token.
  const sensing = byId.get("sensing");
  assert.equal(sensing.token, "failed");
  assert.equal(sensing.label, "1 failed · 1 warning · 2 running");
  assert.equal(sensing.members, 4);
});

test("a group is dead when every part is down", () => {
  const states = new Map([
    ["/s/l/a", state("ok", "stopped")],
    ["/s/l/b", state("failed", "failed")],
  ]);
  const byId = rollup(ROOT, states);
  assert.equal(byId.get("lidar").dead, "every member down");
  assert.equal(byId.get("sensing").dead, "every member down");
  assert.equal(byId.get("lidar").members, 2);
  assert.equal(rollup(ROOT, new Map()).size, 0);
  assert.equal(summarize([{ token: "idle", members: 1 }]).label, "1 idle");
});

test("a group carries the busiest log within and no restart count", () => {
  const states = new Map([
    [
      "/s/l/a",
      {
        ...state("running"),
        restarts: { restarts: 2, respawns: 1 },
        rate: 100,
        busy: "",
      },
    ],
    ["/s/l/b", { ...state("running"), rate: 9000, busy: "log 8.8 KB/s" }],
    [
      "/s/cam",
      {
        ...state("running"),
        restarts: { restarts: 0, respawns: 4 },
        rate: 5000,
        busy: "log 4.9 KB/s",
      },
    ],
  ]);
  const byId = rollup(ROOT, states);
  assert.equal(byId.get("lidar").restarts, null);
  assert.equal(byId.get("lidar").busy, "log 8.8 KB/s");
  assert.equal(byId.get("sensing").restarts, null);
  assert.equal(byId.get("sensing").rate, 9000);
  assert.equal(byId.get("cam").restarts.respawns, 4);
});
