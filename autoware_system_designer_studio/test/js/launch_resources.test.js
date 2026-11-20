// Unit tests for the resource feed's rules: window filter, member and host
// use over a window, icon levels, and the idle block of the Resources view.

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
const { groupIdle } = global.window.SD.activities.launch.resourceMapRules;

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
    { cpu: 2, mem: 3, gpu: 0 },
  );
  assert.deepEqual(levels(null, host), { cpu: 0, mem: 0, gpu: 0 });
  assert.equal(levels({ cpu: 2, rss_kb: 0, gpu_mem_kb: 5000 }, host).gpu, 2);
});

test("a unit's label sums up its host and GPU", () => {
  const host = hostUsage([reading(1, {}, { cpu: 2.5 })]);
  assert.equal(hostLabel(host), "CPU 2.5/8 · RAM 0.5/1.0 GB · GPU 20 %");
});

test("nodes under a share of their unit gather in one idle block", () => {
  const node = (name, extra = {}) => ({
    entity_type: "node",
    name,
    path: `/m/${name}`,
    unique_id: name,
    ...extra,
  });
  const section = {
    name: "main_ecu",
    nodes: 4,
    children: [
      {
        entity_type: "module",
        name: "m",
        unique_id: "m",
        children: [node("busy"), node("quiet"), node("zero")],
      },
      {
        entity_type: "module",
        name: "q",
        unique_id: "q",
        children: [node("lone")],
      },
    ],
  };
  const values = { "/m/busy": 100, "/m/quiet": 0.5, "/m/lone": 2 };
  const grouped = groupIdle(section, (n) => values[n.path] || 0);
  const [kept, lone, idle] = grouped.children;
  assert.deepEqual(
    kept.children.map((child) => child.name),
    ["busy"],
  );
  assert.equal(lone.children[0].name, "lone");
  assert.equal(idle.idle, true);
  assert.deepEqual(
    idle.members.map((member) => member.name),
    ["quiet", "zero"],
  );
  assert.ok(idle.weight > 0);
  // Without any value the whole unit is idle.
  const empty = groupIdle(section, () => 0);
  assert.equal(empty.children.length, 1);
  assert.equal(empty.children[0].members.length, 4);
});
