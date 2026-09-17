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

// ── Recorded graph ──────────────────────────────────────────────────────────

test("the recorded graph is built from timers, triggers, responses and links", () => {
  const design = graphV2();
  const measurement = L.fromJson(fileV2(), "Runtime_latency.json");
  const recorded = measurement.recordedGraph(design);
  const g = recorded.graph;

  const timer = "r:/sensing/a|timer:100";
  const aGate = "r:/sensing/a|run:/sensing/cloud";
  const aPub = "r:/sensing/a|pub:/sensing/cloud";
  const bSub = "r:/perception/b|sub:/sensing/cloud";
  const bGate = "r:/perception/b|run:/perception/objects";
  assert.deepEqual(g.clockRootIds, [timer]);
  assert.equal(g.events.get(timer).type, "periodic");
  assert.equal(g.events.get(timer).frequency, 10);
  assert.equal(g.events.get(aGate).type, "timer");
  assert.equal(g.events.get(bGate).type, "input");
  assert.equal(g.events.get(bGate).frequency, 10);
  assert.ok(g.edgeId(timer, aGate));
  assert.ok(g.edgeId(aGate, aPub));
  assert.ok(g.edgeId(aPub, bSub));
  assert.ok(g.edgeId(bSub, bGate));
  assert.equal(recorded.edgeInfo(timer, aGate).kind, "timer");
  assert.equal(recorded.edgeInfo(aPub, bSub).kind, "link");
  assert.equal(recorded.edgeInfo(bSub, bGate).kind, "trigger");
  assert.equal(recorded.edgeInfo(bSub, bGate).share, 1);
  assert.equal(recorded.edgeInfo(aGate, bGate), null);

  // Instances come from the design: the node keeps its name and path.
  assert.equal(g.ownerOf(bGate).name, "b");
  assert.equal(g.ownerOf(bGate).path, "/perception/b");
  assert.deepEqual(L.outputTopicsOf(g, bGate && g.events.get(bGate)), [
    "/perception/objects",
  ]);

  const { costs } = recorded;
  assert.equal(costs.exec(g.events.get(bGate)).max, 5);
  assert.equal(costs.exec(g.events.get(bGate)).source, "measured");
  assert.equal(costs.exec(g.events.get(timer)).max, 0);
  assert.equal(costs.wait(g.events.get(timer)).max, 0);
  const link = costs.comm(null, g.events.get(aPub), g.events.get(bSub));
  assert.equal(link.source, "measured");
  assert.equal(link.max, 0.9);
  assert.equal(
    costs.comm(null, g.events.get(bSub), g.events.get(bGate)).max,
    0,
  );

  // timer → a runs 0.9 → link 0.9 → b runs 5: the file's own chain record.
  const solution = new T.ChainSolver(g, costs).solve(timer);
  assert.ok(Math.abs(solution.arrivals.get(bGate).total.max - 6.8) < 1e-9);
  assert.equal(recorded.triggerOf(bGate).topic, "/sensing/cloud");
  assert.match(
    recorded.label,
    /1 timers, 2 outputs, 1 links, 0 sampled inputs/,
  );
});

test("a sampled input waits for the gate's next run and keeps its response", () => {
  const design = graphV2();
  const file = fileV2();
  file.nodes[0].timers = [{ period_ms: 50, rate_hz: 20, count: 200 }];
  file.nodes[0].outputs.push({
    topic: "/perception/summary",
    rate_hz: 20,
    trigger: { kind: "timer", period_ms: 50, share: 1 },
    exec: { count: 200, min_ms: 0.1, mean_ms: 0.2, max_ms: 0.4, sd_ms: 0.1 },
    response: [
      {
        from: "/sensing/cloud",
        count: 100,
        min_ms: 1,
        mean_ms: 25,
        max_ms: 51,
      },
    ],
  });
  const recorded = L.fromJson(file).recordedGraph(design);
  const g = recorded.graph;
  const bSub = "r:/perception/b|sub:/sensing/cloud";
  const bTimer = "r:/perception/b|timer:50";
  const summary = "r:/perception/b|run:/perception/summary";

  assert.equal(g.clockRootIds.length, 2);
  const info = recorded.edgeInfo(bSub, summary);
  assert.equal(info.kind, "sampled");
  assert.equal(info.rate, 20);
  assert.equal(info.response.max_ms, 51);
  const sampling = recorded.costs.comm(
    null,
    g.events.get(bSub),
    g.events.get(summary),
  );
  assert.equal(sampling.source, "derived");
  assert.equal(sampling.max, 50);
  assert.equal(sampling.mean, 25);
  assert.equal(recorded.edgeInfo(bTimer, summary).kind, "timer");

  // From a's timer the summary gate is reached through the sampled input.
  const solution = new T.ChainSolver(g, recorded.costs).solve(
    "r:/sensing/a|timer:100",
  );
  assert.ok(Math.abs(solution.arrivals.get(summary).total.max - 52.2) < 1e-9);
  // From b's own timer the gate fires at once.
  const own = new T.ChainSolver(g, recorded.costs).solve(bTimer);
  assert.ok(Math.abs(own.arrivals.get(summary).total.max - 0.4) < 1e-9);
  assert.match(recorded.label, /1 sampled inputs/);
});

test("links attach by the topic's only publisher, intra-process costs nothing, unplaced nodes join the root", () => {
  const design = graphV2();
  const data = fileV2({
    links: [
      {
        topic: "/sensing/cloud",
        subscriber: "/perception/b",
        min_ms: 0.1,
        max_ms: 0.9,
      },
      {
        topic: "/perception/objects",
        publisher: "/perception/b",
        subscriber: "/extra/c",
        intra_process: true,
      },
    ],
  });
  data.nodes.push({
    node_path: "/extra/c",
    inputs: [{ topic: "/perception/objects", rate_hz: 10, count: 100 }],
    timers: [],
    outputs: [
      {
        topic: "/extra/out",
        rate_hz: 10,
        trigger: { kind: "unknown", share: 0.5 },
        response: [
          {
            from: "/perception/objects",
            count: 100,
            min_ms: 1,
            mean_ms: 2,
            max_ms: 3,
          },
        ],
      },
    ],
    declared_diff: [],
  });
  const recorded = L.fromJson(data).recordedGraph(design);
  const g = recorded.graph;
  const aPub = "r:/sensing/a|pub:/sensing/cloud";
  const bSub = "r:/perception/b|sub:/sensing/cloud";
  const bPub = "r:/perception/b|pub:/perception/objects";
  const cSub = "r:/extra/c|sub:/perception/objects";
  const cGate = "r:/extra/c|run:/extra/out";
  assert.equal(recorded.edgeInfo(aPub, bSub).kind, "link");
  assert.equal(recorded.edgeInfo(bPub, cSub).kind, "intra");
  assert.equal(
    recorded.costs.comm(null, g.events.get(bPub), g.events.get(cSub)).source,
    "intra_process",
  );
  assert.ok(g.instanceByPath.has("/extra/c"));
  assert.equal(g.ownerOf(cGate).name, "c");
  assert.equal(g.events.get(cGate).type, null);
  assert.equal(recorded.costs.exec(g.events.get(cGate)).source, "unmeasured");
  assert.equal(recorded.edgeInfo(cSub, cGate).kind, "sampled");

  const solution = new T.ChainSolver(g, recorded.costs).solve(
    "r:/sensing/a|timer:100",
  );
  assert.ok(solution.reach.has(cGate));
  assert.deepEqual(solution.unknownGates, [cGate]);
  assert.throws(
    () =>
      L.fromJson(file({ schema: L.SCHEMA_V1, processes: [] })).recordedGraph(
        design,
      ),
    /latency\/2/,
  );
});
