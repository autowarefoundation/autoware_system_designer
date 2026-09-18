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
        in_ports: [
          { name: "cloud", topic: ["sensing", "cloud"], event: input },
        ],
        out_ports: [],
        events: [run],
      },
    ],
  });
}

// A latency/1 file: processes keyed by node path and process name.
const file = (overrides = {}) => ({
  schema: L.SCHEMA_V1,
  mode: "Runtime",
  processes: [
    {
      node_path: "/perception/b",
      process: "run",
      min_ms: 1,
      mean_ms: 2,
      max_ms: 5,
      sd_ms: 0.5,
      count: 100,
    },
    { node_path: "/nowhere", process: "ghost", min_ms: 1, max_ms: 2 },
  ],
  links: [
    {
      topic: "/sensing/cloud",
      subscriber: "/perception/b",
      min_ms: 0.1,
      mean_ms: 0.2,
      max_ms: 0.9,
    },
  ],
  ...overrides,
});

test("rejects a file with another schema or a malformed record", () => {
  assert.throws(
    () => L.fromJson({ schema: "other" }),
    /unknown measurement schema/,
  );
  assert.throws(
    () =>
      L.fromJson(
        file({ processes: [{ node_path: "/x", min_ms: 1, max_ms: 2 }] }),
      ),
    /lacks process/,
  );
  assert.throws(
    () =>
      L.fromJson(
        file({
          processes: [{ node_path: "/x", process: "p", min_ms: 3, max_ms: 2 }],
        }),
      ),
    /min_ms above max_ms/,
  );
});

// A latency/2 file: the runtime's shape, keyed by node path and topic.
const fileV2 = (overrides = {}) => ({
  schema: L.SCHEMA,
  mode: "Runtime",
  run: { window_s: 10, probe: true },
  nodes: [
    {
      node_path: "/perception/b",
      inputs: [{ topic: "/sensing/cloud", rate_hz: 10, count: 100 }],
      timers: [],
      outputs: [
        {
          topic: "/perception/objects",
          rate_hz: 10,
          trigger: { kind: "input", topic: "/sensing/cloud", share: 1 },
          exec: { count: 100, min_ms: 1, mean_ms: 2, max_ms: 5, sd_ms: 0.5 },
        },
      ],
      declared_diff: [
        {
          output: "/perception/objects",
          declared: "input(/sensing/cloud) on_input",
          observed: "input /sensing/cloud @ 10.0 Hz",
          status: "match",
        },
      ],
    },
    {
      node_path: "/sensing/a",
      inputs: [],
      timers: [{ period_ms: 100, rate_hz: 10, count: 100 }],
      outputs: [
        {
          topic: "/sensing/cloud",
          rate_hz: 10,
          trigger: { kind: "timer", period_ms: 100, share: 1 },
          exec: {
            count: 100,
            min_ms: 0.5,
            mean_ms: 0.6,
            max_ms: 0.9,
            sd_ms: 0.1,
          },
        },
      ],
      declared_diff: [],
    },
  ],
  links: [
    {
      topic: "/sensing/cloud",
      publisher: "/sensing/a",
      subscriber: "/perception/b",
      count: 100,
      min_ms: 0.1,
      mean_ms: 0.2,
      max_ms: 0.9,
      sd_ms: 0.1,
    },
  ],
  chains: [
    {
      from: "/sensing/a:timer:100",
      to: "/sensing/a:/sensing/cloud",
      hops: 1,
      terminal: false,
      count: 100,
      min_ms: 0.5,
      mean_ms: 0.6,
      max_ms: 0.9,
      sd_ms: 0.1,
    },
    {
      from: "/sensing/a:timer:100",
      to: "/perception/b:/perception/objects",
      hops: 2,
      terminal: true,
      count: 100,
      min_ms: 1.6,
      mean_ms: 2.8,
      max_ms: 6.8,
      sd_ms: 0.6,
    },
  ],
  ...overrides,
});

function graphV2() {
  // /sensing/a ticks → publishes /sensing/cloud → /perception/b runs → /perception/objects
  const data = {
    unique_id: "root",
    path: "/",
    children: [
      {
        unique_id: "n.a",
        name: "a",
        path: "/sensing/a",
        entity_type: "node",
        in_ports: [],
        out_ports: [
          {
            name: "cloud",
            topic: ["sensing", "cloud"],
            event: {
              unique_id: "a.out",
              name: "output_cloud",
              type: "to_output",
              trigger_ids: ["a.scan"],
              action_ids: ["b.in"],
            },
          },
        ],
        events: [
          {
            unique_id: "a.scan",
            name: "scan",
            type: "periodic",
            frequency: 10,
            trigger_ids: [],
            action_ids: ["a.out"],
          },
        ],
      },
      {
        unique_id: "n.b",
        name: "b",
        path: "/perception/b",
        entity_type: "node",
        in_ports: [
          {
            name: "cloud",
            topic: ["sensing", "cloud"],
            event: {
              unique_id: "b.in",
              name: "input_cloud",
              type: "on_input",
              trigger_ids: ["a.out"],
              action_ids: ["b.run"],
            },
          },
        ],
        out_ports: [
          {
            name: "objects",
            topic: ["perception", "objects"],
            event: {
              unique_id: "b.out",
              name: "output_objects",
              type: "to_output",
              trigger_ids: ["b.run"],
              action_ids: [],
            },
          },
        ],
        events: [
          {
            unique_id: "b.run",
            name: "run",
            type: "on_input",
            trigger_ids: ["b.in"],
            action_ids: ["b.out"],
          },
        ],
      },
    ],
  };
  return new EventGraph().build(data);
}

test("latency/2 exec is found through the output a gate feeds", () => {
  const g = graphV2();
  const measurement = L.fromJson(fileV2(), "Runtime_latency.json");
  const costs = measurement.costs(g);

  const run = costs.exec(g.events.get("b.run"));
  assert.equal(run.source, "measured");
  assert.equal(run.max, 5);
  assert.equal(run.count, 100);
  const scan = costs.exec(g.events.get("a.scan"));
  assert.equal(scan.mean, 0.6);
  assert.equal(costs.exec(g.events.get("a.out")), null);

  const comm = costs.comm(
    g.edgeById.get(g.edgeId("a.out", "b.in")),
    g.events.get("a.out"),
    g.events.get("b.in"),
  );
  assert.equal(comm.source, "measured");
  assert.equal(comm.max, 0.9);
  assert.deepEqual(measurement.matched, {
    processes: 2,
    processesTotal: 2,
    links: 1,
    linksTotal: 1,
  });
  assert.match(measurement.label, /2\/2 outputs, 1\/1 links/);
});

test("an intra-process link costs nothing and is marked", () => {
  const g = graphV2();
  const measurement = L.fromJson(
    fileV2({
      links: [
        {
          topic: "/sensing/cloud",
          publisher: "/sensing/a",
          subscriber: "/perception/b",
          intra_process: true,
        },
      ],
    }),
  );
  const costs = measurement.costs(g);
  const comm = costs.comm(
    g.edgeById.get(g.edgeId("a.out", "b.in")),
    g.events.get("a.out"),
    g.events.get("b.in"),
  );
  assert.equal(comm.source, "intra_process");
  assert.equal(comm.max, 0);
  assert.equal(measurement.matched.links, 1);
  assert.equal(measurement.matched.linksTotal, 1);
});

test("measured chains and the declared diff are looked up by node and topic", () => {
  const g = graphV2();
  const measurement = L.fromJson(fileV2());

  const e2e = measurement.chainFor(
    "/sensing/a",
    "/perception/b",
    "/perception/objects",
  );
  assert.equal(e2e.hops, 2);
  assert.equal(e2e.summary.max, 6.8);
  const deepest = measurement.chainFor("/sensing/a", "/perception/b");
  assert.equal(deepest.to, "/perception/b:/perception/objects");
  assert.equal(measurement.chainFor("/sensing/a", "/nowhere"), null);
  assert.equal(measurement.chainFor("/perception/b", "/perception/b"), null);

  assert.deepEqual(L.outputTopicsOf(g, g.events.get("b.run")), [
    "/perception/objects",
  ]);
  const rows = measurement.diffFor("/perception/b", ["/perception/objects"]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].status, "match");
  assert.deepEqual(measurement.diffFor("/perception/b", ["/other"]), []);
  assert.equal(measurement.nodeRecord("/sensing/a").timers[0].period_ms, 100);
  assert.equal(measurement.nodeRecord("/none"), null);
});

test("latency/2 records are validated", () => {
  assert.throws(
    () => L.fromJson(fileV2({ nodes: [{ outputs: [] }] })),
    /lacks node_path/,
  );
  assert.throws(
    () =>
      L.fromJson(
        fileV2({
          nodes: [
            {
              node_path: "/x",
              outputs: [{ topic: "/t", exec: { min_ms: 3, max_ms: 2 } }],
            },
          ],
        }),
      ),
    /min_ms above max_ms/,
  );
  assert.throws(
    () => L.fromJson(fileV2({ links: [{ topic: "/t" }] })),
    /lacks min_ms/,
  );
  assert.throws(
    () => L.fromJson(fileV2({ chains: [{ from: "a", min_ms: 1, max_ms: 2 }] })),
    /lacks to/,
  );
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

  const comm = costs.comm(
    g.edgeById.get(g.edgeId("a.out", "b.in")),
    g.events.get("a.out"),
    g.events.get("b.in"),
  );
  assert.equal(comm.source, "measured");
  assert.equal(comm.missingSd, 1);
  assert.equal(
    costs.comm(null, g.events.get("b.in"), g.events.get("b.run")),
    null,
  );

  assert.deepEqual(measurement.matched, {
    processes: 1,
    processesTotal: 2,
    links: 1,
    linksTotal: 1,
  });
  assert.match(measurement.label, /1\/2 processes, 1\/1 links/);
});

test("measured costs feed the solver with the design filling the gaps", () => {
  const g = graph();
  const measurement = L.fromJson(file());
  const solution = new T.ChainSolver(
    g,
    T.measuredCosts(g, measurement.costs(g)),
  ).solve("a.scan");
  const run = solution.arrivals.get("b.run");
  assert.equal(run.exec.source, "measured");
  assert.equal(solution.arrivals.get("a.scan").wait.source, "derived");
  // 100 ms period + 0.9 ms link + 5 ms run
  assert.ok(Math.abs(run.total.max - 105.9) < 1e-9);
});

// ── In→out time on the design graph ─────────────────────────────────────────

// A response row: the node's measured time from an input's arrival to the
// publish of an output. The wait an input spends before the run is that
// response less the run.
const withResponse = (overrides = {}) => {
  const data = fileV2();
  data.nodes[0].outputs[0].response = [
    {
      from: "/sensing/cloud",
      count: 100,
      min_ms: 1,
      mean_ms: 12,
      max_ms: 55,
      sd_ms: 3,
    },
  ];
  return { ...data, ...overrides };
};

test("an input→gate edge costs the measured in→out response less the run", () => {
  const g = graphV2();
  const measurement = L.fromJson(withResponse());
  const costs = measurement.costs(g);
  const edge = g.edgeById.get(g.edgeId("b.in", "b.run"));
  const wait = costs.comm(edge, g.events.get("b.in"), g.events.get("b.run"));
  assert.equal(wait.source, "derived");
  // response (1, 12, 55) less run (1, 2, 5)
  assert.deepEqual([wait.min, wait.mean, wait.max], [0, 10, 50]);
  assert.ok(Math.abs(wait.sd - Math.sqrt(9 - 0.25)) < 1e-9);
  assert.equal(wait.count, 100);
  // Without a response row the edge is not measured.
  const plain = L.fromJson(fileV2()).costs(g);
  assert.equal(
    plain.comm(edge, g.events.get("b.in"), g.events.get("b.run")),
    null,
  );
});

test("an observed gate waits nothing of its own; an unobserved one keeps the design's", () => {
  const g = graphV2();
  const costs = L.fromJson(withResponse()).costs(g);
  assert.equal(costs.wait(g.events.get("a.scan")).max, 0);
  assert.equal(costs.wait(g.events.get("b.run")).max, 0);
  const partial = L.fromJson(withResponse({ nodes: [] })).costs(g);
  assert.equal(partial.wait(g.events.get("a.scan")), null);
  const solved = new T.ChainSolver(g, T.measuredCosts(g, partial)).solve(
    "a.scan",
  );
  assert.equal(solved.arrivals.get("a.scan").wait.source, "derived");
});

test("a measured chain follows the design's events with the file's time", () => {
  const g = graphV2();
  const measurement = L.fromJson(withResponse());
  const solution = new T.ChainSolver(
    g,
    T.measuredCosts(g, measurement.costs(g)),
  ).solve("a.scan");
  const run = solution.arrivals.get("b.run");
  // scan run 0.9 + link 0.9 + wait 50 + run 5, no sampling of the source clock
  assert.ok(Math.abs(run.total.max - 56.8) < 1e-9);
  assert.ok(Math.abs(run.total.mean - (0.6 + 0.2 + 10 + 2)) < 1e-9);
  assert.equal(solution.arrivals.get("a.scan").wait.max, 0);
  assert.deepEqual([...solution.reach].sort(), [
    "a.out",
    "a.scan",
    "b.in",
    "b.out",
    "b.run",
  ]);
});

test("hopInfo and triggerOf expose the records behind an edge and a gate", () => {
  const g = graphV2();
  const measurement = L.fromJson(withResponse());
  const linkEdge = g.edgeById.get(g.edgeId("a.out", "b.in"));
  const link = measurement.hopInfo(
    g,
    linkEdge,
    g.events.get("a.out"),
    g.events.get("b.in"),
  );
  assert.equal(link.link.max_ms, 0.9);
  const waitEdge = g.edgeById.get(g.edgeId("b.in", "b.run"));
  const wait = measurement.hopInfo(
    g,
    waitEdge,
    g.events.get("b.in"),
    g.events.get("b.run"),
  );
  assert.equal(wait.response.from, "/sensing/cloud");
  assert.equal(wait.run.max_ms, 5);
  assert.equal(wait.outTopic, "/perception/objects");
  assert.deepEqual(measurement.triggerOf(g, g.events.get("b.run")), {
    kind: "input",
    topic: "/sensing/cloud",
    share: 1,
  });
  assert.equal(measurement.triggerOf(g, g.events.get("b.in")), null);
  assert.throws(
    () =>
      L.fromJson(
        fileV2({
          nodes: [
            {
              node_path: "/x",
              outputs: [{ topic: "/t", response: [{ from: "/u", min_ms: 1 }] }],
            },
          ],
        }),
      ),
    /lacks max_ms/,
  );
});

// ── Loading ─────────────────────────────────────────────────────────────────

// The bundle serves the file as a script assigning window.latencyData[mode],
// which loads from file:// where JSON cannot be fetched.
function fakeDocument(onAppend) {
  return {
    head: {
      appendChild(script) {
        setImmediate(() => onAppend(script));
      },
    },
    createElement() {
      return {};
    },
  };
}

const scriptText = (mode, data) =>
  `window.latencyData = window.latencyData || {};\n` +
  `window.latencyData[${JSON.stringify(mode)}] = ${JSON.stringify(data)};\n`;

test("parseText reads the bundle's script and bare JSON alike", () => {
  const data = fileV2();
  assert.deepEqual(L.parseText(scriptText("Run time", data)), data);
  assert.deepEqual(L.parseText(JSON.stringify(data)), data);
  assert.throws(() => L.parseText("not json"), SyntaxError);
});

test("fromFile accepts a dropped .js or .json file", async () => {
  const asFile = (name, text) => ({ name, text: async () => text });
  const js = await L.fromFile(
    asFile("Runtime_latency.js", scriptText("Runtime", fileV2())),
  );
  assert.equal(js.label, "Runtime_latency.js");
  assert.equal(js.chains.length, 2);
  const json = await L.fromFile(asFile("x.json", JSON.stringify(fileV2())));
  assert.equal(json.mode, "Runtime");
});

test("loadBundled loads data/<mode>_latency.js", async () => {
  const saved = global.document;
  global.document = fakeDocument((script) => {
    assert.equal(script.src, "data/Runtime_latency.js");
    window.latencyData = { Runtime: fileV2() };
    script.onload();
  });
  try {
    const measured = await L.loadBundled("Runtime");
    assert.equal(measured.label, "Runtime_latency.js");
    assert.equal(measured.chains.length, 2);
  } finally {
    global.document = saved;
    delete window.latencyData;
  }
});

test("loadBundled resolves null when the file is absent", async () => {
  const saved = global.document;
  global.document = fakeDocument((script) => script.onerror());
  try {
    assert.equal(await L.loadBundled("Runtime"), null);
  } finally {
    global.document = saved;
  }
});

test("labels say when a run counted ROS time", () => {
  assert.equal(L.clockNote({ clock: { base: "wall" } }), "");
  assert.equal(L.clockNote(null), "");
  assert.equal(
    L.clockNote({ clock: { base: "ros", rate: 0.4998, samples: 12 } }),
    ", ROS time ×0.5",
  );
  const g = new EventGraph();
  g.build(graphV2());
  const measured = L.fromJson(
    fileV2({ run: { window_s: 10, clock: { base: "ros", rate: 1 } } }),
  );
  measured.costs(g);
  assert.match(measured.label, /links matched, ROS time ×1\)$/);
});

// ── Gates the record says never ran ─────────────────────────────────────────

test("a gate is dead when its node exited, never initialized or was never observed", () => {
  const g = graphV2();
  const exited = fileV2();
  exited.nodes[0].process = {
    pids: [7],
    state: "exited",
    exit: { at: "2026-09-18T02:35:13+00:00", code: -6 },
  };
  let m = L.fromJson(exited);
  assert.equal(
    m.deadReason(g, g.events.get("b.run")),
    "process exited (code -6) at 2026-09-18T02:35:13+00:00",
  );
  assert.equal(m.deadReason(g, g.events.get("a.scan")), null);
  assert.equal(m.deadReason(g, g.events.get("b.in")), null); // ports are never dead

  const stalled = fileV2();
  stalled.nodes[0].process = { pids: [7], state: "not_initialized" };
  m = L.fromJson(stalled);
  assert.match(
    m.deadReason(g, g.events.get("b.run")),
    /never left construction/,
  );

  const missing = fileV2({ unobserved_nodes: ["/perception/b"] });
  m = L.fromJson(missing);
  assert.equal(
    m.deadReason(g, g.events.get("b.run")),
    "node not observed in the run",
  );

  // A running node with its output recorded is alive; a latency/1 file knows nothing of this.
  assert.equal(L.fromJson(fileV2()).deadReason(g, g.events.get("b.run")), null);
  assert.equal(L.fromJson(file()).deadReason(g, g.events.get("b.run")), null);
});

test("a gate is dead when every output it feeds was declared and never published", () => {
  const g = graphV2();
  const silent = fileV2();
  silent.nodes[0].outputs = [];
  silent.nodes[0].declared_diff = [
    {
      output: "/perception/objects",
      declared: "input(/sensing/cloud) on_input",
      observed: "not published",
      status: "unobserved",
    },
  ];
  const m = L.fromJson(silent);
  assert.equal(
    m.deadReason(g, g.events.get("b.run")),
    "output never published (/perception/objects)",
  );
  const costs = m.costs(g);
  const exec = costs.exec(g.events.get("b.run"));
  assert.equal(exec.source, "dead");
  assert.equal(exec.dead, true);
  assert.equal(costs.wait(g.events.get("b.run")).max, 0);
  const solution = new T.ChainSolver(g, T.measuredCosts(g, costs)).solve(
    "a.scan",
  );
  assert.equal(solution.arrivals.get("b.run").total.dead, true);
  assert.equal(solution.arrivals.get("a.scan").total.dead, false);

  // A row of another status keeps the gate alive though unmeasured.
  silent.nodes[0].declared_diff[0].status = "undeclared";
  const alive = L.fromJson(silent).costs(g);
  assert.equal(alive.exec(g.events.get("b.run")), null);
});
