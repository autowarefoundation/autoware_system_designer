// Unit tests for the measurement loader: validation, key matching, coverage.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const JS_DIR = path.join(
  __dirname,
  "..",
  "..",
  "autoware_system_designer",
  "visualizer",
  "js",
);

// The browser modules attach to window; under node they export, except the
// loader reads the timing model off window, so both are provided.
global.window = { location: { protocol: "http:" } };
const T = require(path.join(JS_DIR, "timing_model.js"));
global.window.TimingModel = T;
const { EventGraph } = require(path.join(JS_DIR, "event_graph.js"));
const L = require(path.join(JS_DIR, "latency_source.js"));

function graph() {
  const scan = {
    unique_id: "a.scan",
    name: "scan",
    type: "periodic",
    frequency: 10,
    trigger_ids: [],
    action_ids: ["a.out"],
  };
  const out = {
    unique_id: "a.out",
    name: "output_cloud",
    type: "to_output",
    trigger_ids: ["a.scan"],
    action_ids: ["b.in"],
  };
  const input = {
    unique_id: "b.in",
    name: "input_cloud",
    type: "on_input",
    trigger_ids: ["a.out"],
    action_ids: ["b.run"],
  };
  const run = {
    unique_id: "b.run",
    name: "run",
    type: "on_input",
    trigger_ids: ["b.in"],
    action_ids: [],
  };
  return new EventGraph().build({
    unique_id: "root",
    path: "/",
    children: [
      {
        unique_id: "n.a",
        name: "a",
        path: "/sensing/a",
        entity_type: "node",
        in_ports: [],
        out_ports: [{ name: "cloud", topic: ["sensing", "cloud"], event: out }],
        events: [scan],
      },
      {
        unique_id: "n.b",
        name: "b",
        path: "/perception/b",
        entity_type: "node",
        in_ports: [{ name: "cloud", topic: ["sensing", "cloud"], event: input }],
        out_ports: [],
        events: [run],
      },
    ],
  });
}

const file = (overrides = {}) => ({
  schema: L.SCHEMA,
  mode: "Runtime",
  source: "caret",
  processes: [
    { node_path: "/perception/b", process: "run", min_ms: 1, mean_ms: 2, max_ms: 5, sd_ms: 0.5, count: 100 },
    { node_path: "/nowhere", process: "ghost", min_ms: 1, max_ms: 2 },
  ],
  links: [{ topic: "/sensing/cloud", subscriber: "/perception/b", min_ms: 0.1, mean_ms: 0.2, max_ms: 0.9 }],
  ...overrides,
});

test("rejects a file with another schema or a malformed record", () => {
  assert.throws(() => L.fromJson({ schema: "other" }), /unknown measurement schema/);
  assert.throws(
    () => L.fromJson(file({ processes: [{ node_path: "/x", min_ms: 1, max_ms: 2 }] })),
    /lacks process/,
  );
  assert.throws(
    () => L.fromJson(file({ processes: [{ node_path: "/x", process: "p", min_ms: 3, max_ms: 2 }] })),
    /min_ms above max_ms/,
  );
});

test("the CARET converter is a reserved seam", () => {
  assert.throws(() => L.fromJson({ source: "caret", runs: [] }), /not implemented/);
});

test("exec matches by node path and process, comm by topic with open ends", () => {
  const g = graph();
  const measurement = L.fromJson(file(), "run.json");
  const costs = measurement.costs(g);
  const exec = costs.exec(g.events.get("b.run"));
  assert.equal(exec.source, "measured");
  assert.equal(exec.max, 5);
  assert.equal(exec.count, 100);
  assert.equal(costs.exec(g.events.get("a.scan")), null);

  const comm = costs.comm(g.edgeById.get(g.edgeId("a.out", "b.in")), g.events.get("a.out"), g.events.get("b.in"));
  assert.equal(comm.source, "measured");
  assert.equal(comm.missingSd, 1);
  assert.equal(costs.comm(null, g.events.get("b.in"), g.events.get("b.run")), null);

  assert.deepEqual(measurement.matched, { processes: 1, processesTotal: 2, links: 1, linksTotal: 1 });
  assert.match(measurement.label, /1\/2 processes, 1\/1 links/);
});

test("measured costs feed the solver with declared fallback per hop", () => {
  const g = graph();
  const measurement = L.fromJson(file());
  const solution = new T.ChainSolver(g, T.measuredCosts(g, measurement.costs(g))).solve("a.scan");
  const run = solution.arrivals.get("b.run");
  assert.equal(run.exec.source, "measured");
  assert.equal(solution.arrivals.get("a.scan").wait.source, "derived");
  // 100 ms period + 0.9 ms link + 5 ms run
  assert.ok(Math.abs(run.total.max - 105.9) < 1e-9);
});
