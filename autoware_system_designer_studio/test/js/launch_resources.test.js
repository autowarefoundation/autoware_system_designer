// Unit tests for the resource feed's rules: window filter, member and host
// use over a window, icon levels, and the low blocks of the Resources view.

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

global.window = {
  SD: {
    activities: {},
    widgets: { element: () => ({}), segmented: () => ({}) },
  },
};
require(path.join(JS_DIR, "activities", "launch", "resources.js"));
require(path.join(JS_DIR, "activities", "launch", "resource_map.js"));
const { within, memberUsage, hostUsage, levels, hostLabel } =
  global.window.SD.activities.launch.resourceUsage;
const { groupLow, nestContainers } =
  global.window.SD.activities.launch.resourceMapRules;

function reading(second, members, host = {}) {
  return {
    time_ns: second * 1e9,
    interval_s: 1,
    host: { cpus: 8, mem_total_kb: 1000000, mem_used_kb: 500000, ...host },
    gpu: {
      source: "nvml",
      per_process: true,
      devices: [{ index: 0, util: 20, mem_total_kb: 100000 }],
    },
    members,
  };
}

test("the window keeps the readings within its span of the latest", () => {
  const readings = [1, 5, 9, 10, 11].map((s) => reading(s, {}));
  assert.deepEqual(
    within(readings, 5).map((r) => r.time_ns / 1e9),
    [9, 10, 11],
  );
  assert.deepEqual(within([], 5), []);
});

test("a member's use is the mean over the window, with the RSS peak and latest PSS", () => {
  const readings = [
    reading(1, { "/n": { pid: 4, procs: 1, threads: 3, rss_kb: 100 } }),
    reading(2, {
      "/n": { pid: 4, procs: 1, threads: 3, rss_kb: 300, cpu: 0.5, pss_kb: 90 },
    }),
    reading(3, {
      "/n": { pid: 4, procs: 1, threads: 3, rss_kb: 200, cpu: 1.5 },
    }),
  ];
  const usage = memberUsage(readings, "/n");
  assert.equal(usage.cpu, 1);
  assert.equal(usage.rss_kb, 200);
  assert.equal(usage.rss_peak_kb, 300);
  assert.equal(usage.pss_kb, 90);
  assert.equal(usage.gpu_mem_kb, null);
  assert.equal(usage.span_s, 2);
  assert.equal(memberUsage(readings, "/other"), null);
});

test("levels count cores of CPU and shares of the device's memory", () => {
  const host = hostUsage([reading(1, {})]);
  assert.deepEqual(
    levels({ cpu: 0.6, rss_kb: 100000, gpu_mem_kb: 1000 }, host),
    {
      cpu: 2,
      mem: 3,
      gpu: 0,
    },
  );
  assert.deepEqual(levels(null, host), { cpu: 0, mem: 0, gpu: 0 });
  assert.equal(levels({ cpu: 2, rss_kb: 0, gpu_mem_kb: 5000 }, host).gpu, 2);
});

test("a unit's label sums up its host and GPU", () => {
  const host = hostUsage([reading(1, {}, { cpu: 2.5 })]);
  assert.equal(hostLabel(host), "CPU 2.5/8 · RAM 0.5/1.0 GB · GPU 20 %");
});

test("nodes under a share of their unit gather in one low block per module", () => {
  const node = (name, module = "m") => ({
    entity_type: "node",
    name,
    path: `/${module}/${name}`,
    unique_id: name,
  });
  const section = {
    name: "main_ecu",
    nodes: 5,
    children: [
      {
        entity_type: "module",
        name: "m",
        path: "/m",
        unique_id: "m",
        children: [node("busy"), node("quiet"), node("zero")],
      },
      {
        entity_type: "module",
        name: "q",
        path: "/q",
        unique_id: "q",
        children: [node("lone", "q"), node("near", "q")],
      },
    ],
  };
  // Total 100: cut 0.2, keep cut 0.1.
  const values = {
    "/m/busy": 96.9,
    "/m/quiet": 0.15,
    "/q/lone": 2.8,
    "/q/near": 0.15,
  };
  const valueOf = (n) => values[n.path] || 0;
  const kept = new Set();
  const grouped = groupLow(section, valueOf, { kept });
  const [m, q] = grouped.children;
  assert.deepEqual(
    m.children.map((child) => child.name),
    ["busy", "low"],
  );
  assert.deepEqual(
    m.children[1].members.map((member) => member.name),
    ["quiet", "zero"],
  );
  assert.equal(m.children[1].owner, "/m");
  assert.ok(m.children[1].weight > 0);
  assert.deepEqual(
    q.children.map((child) => child.name),
    ["lone", "low"],
  );
  assert.deepEqual([...kept].sort(), ["/m/busy", "/q/lone"]);
  // A node holding a block keeps it down to the keep cut, a zero never.
  const again = groupLow(section, valueOf, {
    shown: new Set(["/q/near", "/m/zero"]),
  });
  assert.deepEqual(
    again.children[1].children.map((child) => child.name),
    ["lone", "near"],
  );
  assert.deepEqual(
    again.children[0].children[1].members.map((member) => member.name),
    ["quiet", "zero"],
  );
  // Without any value every module holds one low block.
  const empty = groupLow(section, () => 0);
  assert.deepEqual(
    empty.children.map((child) => child.children.map((c) => c.members.length)),
    [[3], [2]],
  );
  // Held nodes have no block, low or not, and do not count in the total.
  const held = groupLow(section, valueOf, {
    held: (n) => n.name === "quiet" || n.name === "zero",
  });
  assert.deepEqual(
    held.children[0].children.map((child) => child.name),
    ["busy"],
  );
});

test("a node container moves into the deepest module of its namespace", () => {
  const container = (name, namespace) => ({
    entity_type: "node",
    name,
    path: `${namespace.replace(/\/$/, "")}/${name}`,
    namespace,
    launcher: { launch_state: "node_container" },
  });
  const section = {
    name: "ecu",
    children: [
      {
        entity_type: "module",
        name: "planning",
        path: "/planning",
        children: [
          {
            entity_type: "module",
            name: "mission_planning",
            path: "/planning/mission_planning",
            children: [{ entity_type: "node", name: "planner" }],
          },
        ],
      },
      container("mission_container", "/planning/mission_planning"),
      container("planning_container", "/planning/other"),
      container("root_container", "/"),
    ],
  };
  const nested = nestContainers(section);
  assert.deepEqual(
    nested.children.map((child) => child.name),
    ["planning", "root_container"],
  );
  const [planning] = nested.children;
  assert.deepEqual(
    planning.children.map((child) => child.name),
    ["mission_planning", "planning_container"],
  );
  assert.deepEqual(
    planning.children[0].children.map((child) => child.name),
    ["planner", "mission_container"],
  );
  assert.equal(section.children.length, 4);
});
