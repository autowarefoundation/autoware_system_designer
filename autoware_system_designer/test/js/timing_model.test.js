// Unit tests for the chain latency solver, on hand-built event graphs.
// Run with: node --test test/js/*.test.js

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
const { EventGraph } = require(path.join(JS_DIR, "event_graph.js"));
const T = require(path.join(JS_DIR, "timing_model.js"));

// ── Graph builder ───────────────────────────────────────────────────────────

// nodes: [{ name, inputs: [name | { name, type }], outputs: [name | { name,
// type }], processes: [{ name, type, frequency, on: [input names], to:
// [output names], after: [process names] }] }]; links: [[nodeA, output,
// nodeB, input]]. A port's type is its message type, "cloud" by default.
function build({ nodes, links = [] }) {
  const events = new Map();
  const event = (id, name, type) => {
    if (!events.has(id)) {
      events.set(id, {
        unique_id: id,
        name,
        type,
        trigger_ids: [],
        action_ids: [],
        frequency: null,
      });
    }
    return events.get(id);
  };
  const trigger = (from, to) => {
    if (!to.trigger_ids.includes(from.unique_id)) {
      to.trigger_ids.push(from.unique_id);
    }
    if (!from.action_ids.includes(to.unique_id)) {
      from.action_ids.push(to.unique_id);
    }
  };

  const port = (spec) =>
    typeof spec === "string" ? { name: spec, type: "cloud" } : spec;
  const children = nodes.map((node) => {
    const inPorts = (node.inputs || []).map(port).map(({ name, type }) => ({
      name,
      msg_type: type,
      topic: [`${node.name}_${name}`],
      event: event(`${node.name}.in.${name}`, `input_${name}`, "on_input"),
    }));
    const outPorts = (node.outputs || []).map(port).map(({ name, type }) => ({
      name,
      msg_type: type,
      topic: [`${node.name}_${name}`],
      event: event(`${node.name}.out.${name}`, `output_${name}`, "to_output"),
    }));
    const processes = (node.processes || []).map((p) => {
      const e = event(`${node.name}.${p.name}`, p.name, p.type ?? null);
      if (p.frequency !== undefined) e.frequency = p.frequency;
      return e;
    });
    (node.processes || []).forEach((p, i) => {
      (p.on || []).forEach((input) =>
        trigger(events.get(`${node.name}.in.${input}`), processes[i]),
      );
      (p.after || []).forEach((name) =>
        trigger(events.get(`${node.name}.${name}`), processes[i]),
      );
      (p.to || []).forEach((output) =>
        trigger(processes[i], events.get(`${node.name}.out.${output}`)),
      );
    });
    return {
      unique_id: `n.${node.name}`,
      name: node.name,
      path: `/${node.component || "comp"}/${node.name}`,
      entity_type: "node",
      in_ports: inPorts,
      out_ports: outPorts,
      events: processes,
      children: [],
    };
  });

  links.forEach(([a, output, b, input]) =>
    trigger(events.get(`${a}.out.${output}`), events.get(`${b}.in.${input}`)),
  );

  return new EventGraph().build({
    unique_id: "root",
    name: "root",
    path: "/",
    entity_type: "system",
    children,
  });
}

const near = (actual, expected, eps = 1e-9) =>
  assert.ok(
    Math.abs(actual - expected) < eps,
    `expected ${expected}, got ${actual}`,
  );

const lat = (min, mean, max, sd) => ({
  min_ms: min,
  mean_ms: mean,
  max_ms: max,
  sd_ms: sd,
});

// A measurement answering exec by event id from records { id: lat }; no comm.
const measuredFrom = (records) => ({
  exec: (event) =>
    records[event.id] ? T.fromRecord(records[event.id], "measured") : null,
  comm: () => null,
});
const measured = (graph, records) =>
  T.measuredCosts(graph, measuredFrom(records));

// ── Summaries ───────────────────────────────────────────────────────────────

test("a periodic gate needs no measurement: uniform over one period", () => {
  const s = T.uniform(0, 100);
  near(s.min, 0);
  near(s.mean, 50);
  near(s.max, 100);
  near(s.sd, 100 / Math.sqrt(12));
  assert.equal(s.missingSd, 0);
});

test("series addition adds components and variances", () => {
  const a = T.summary({ min: 1, mean: 2, max: 3, sd: 3 });
  const b = T.summary({ min: 10, mean: 20, max: 30, sd: 4 });
  const s = T.add(a, b);
  assert.deepEqual([s.min, s.mean, s.max, s.sd], [11, 22, 33, 5]);
  assert.equal(s.approx, false);
});

test("a missing sd is counted, not invented", () => {
  const s = T.fromRecord({ min_ms: 1, mean_ms: 2, max_ms: 3 }, "measured");
  assert.equal(s.sd, 0);
  assert.equal(s.missingSd, 1);
  const sum = T.add(s, T.summary({ min: 1, mean: 1, max: 1, sd: 2 }));
  assert.equal(sum.sd, 2);
  assert.equal(sum.missingSd, 1);
  assert.match(T.formatSummary(sum), /\?/);
});

test("fold: max for and, min for or, sd borrowed from the mean branch", () => {
  const branches = [
    { key: "a", summary: T.summary({ min: 1, mean: 5, max: 50, sd: 1 }) },
    { key: "b", summary: T.summary({ min: 4, mean: 6, max: 20, sd: 9 }) },
  ];
  const and = T.fold(branches, "max");
  assert.deepEqual(
    [and.summary.min, and.summary.mean, and.summary.max, and.summary.sd],
    [4, 6, 50, 9],
  );
  assert.deepEqual(and.via, { min: "b", mean: "b", max: "a" });
  assert.equal(and.summary.approx, true);

  const or = T.fold(branches, "min");
  assert.deepEqual(
    [or.summary.min, or.summary.mean, or.summary.max, or.summary.sd],
    [1, 5, 20, 1],
  );
  assert.deepEqual(or.via, { min: "a", mean: "a", max: "b" });
});

test("axis driver: components and mean + k sigma", () => {
  const s = T.summary({ min: 1, mean: 10, max: 30, sd: 2 });
  assert.equal(T.at(s, "min"), 1);
  assert.equal(T.at(s, "max"), 30);
  assert.equal(T.at(s, "sigma2"), 14);
});

// ── Solver ──────────────────────────────────────────────────────────────────

const chainGraph = () =>
  build({
    nodes: [
      {
        name: "sensor",
        outputs: ["cloud"],
        processes: [
          { name: "scan", type: "periodic", frequency: 10, to: ["cloud"] },
        ],
      },
      {
        name: "filter",
        inputs: ["cloud"],
        outputs: ["clean"],
        processes: [
          {
            name: "run",
            type: "on_input",
            on: ["cloud"],
            to: ["clean"],
          },
        ],
      },
    ],
    links: [["sensor", "cloud", "filter", "cloud"]],
  });
const chainCosts = (graph) =>
  measured(graph, { "filter.run": lat(1, 2, 4, 0.5) });

test("the design alone times only the clocks: a run is an unmeasured placeholder", () => {
  const graph = chainGraph();
  const solution = new T.ChainSolver(graph, T.designCosts(graph)).solve(
    "sensor.scan",
  );
  const run = solution.arrivals.get("filter.run");
  assert.equal(run.exec.source, "unmeasured");
  assert.equal(run.exec.max, 0);
  assert.equal(solution.arrivals.get("sensor.scan").wait.source, "derived");
  near(solution.arrivals.get("filter.out.clean").total.max, 100);
});

test("a two-hop chain: sampling delay plus measured execution", () => {
  const graph = chainGraph();
  const solver = new T.ChainSolver(graph, chainCosts(graph));
  const solution = solver.solve("sensor.scan");
  const sink = solution.arrivals.get("filter.out.clean");
  near(sink.total.min, 1);
  near(sink.total.mean, 52);
  near(sink.total.max, 104);
  near(sink.total.sd, Math.hypot(100 / Math.sqrt(12), 0.5));
  assert.equal(solution.loopEdges.size, 0);
  assert.deepEqual(solution.sinkIds, ["filter.out.clean"]);

  const chain = T.chainTo(solution, "filter.out.clean", "max");
  assert.deepEqual(chain.events, [
    "sensor.scan",
    "sensor.out.cloud",
    "filter.in.cloud",
    "filter.run",
    "filter.out.clean",
  ]);
  assert.equal(chain.edges.length, 4);
  const hops = T.hopsOf(solution, chain);
  assert.equal(hops[3].exec.source, "measured");
  assert.equal(hops[0].wait.source, "derived");
});

test("logical costs give rank, not time", () => {
  const graph = chainGraph();
  const solution = new T.ChainSolver(graph, T.logicalCosts()).solve(
    "sensor.scan",
  );
  assert.equal(solution.arrivals.get("filter.out.clean").total.max, 0);
  assert.equal(solution.arrivals.get("sensor.scan").rank, 0);
  assert.equal(solution.arrivals.get("filter.run").rank, 1);
  assert.equal(solution.arrivals.get("filter.out.clean").rank, 1);
});

const forkGraph = (type) =>
  build({
    nodes: [
      {
        name: "clock",
        outputs: ["tick"],
        processes: [
          { name: "tick", type: "periodic", frequency: 100, to: ["tick"] },
        ],
      },
      {
        name: "fast",
        inputs: ["tick"],
        outputs: ["out"],
        processes: [
          {
            name: "run",
            type: "on_input",
            on: ["tick"],
            to: ["out"],
          },
        ],
      },
      {
        name: "slow",
        inputs: ["tick"],
        outputs: ["out"],
        processes: [
          {
            name: "run",
            type: "on_input",
            on: ["tick"],
            to: ["out"],
          },
        ],
      },
      {
        name: "merge",
        inputs: ["a", "b"],
        outputs: ["out"],
        processes: [{ name: "join", type, on: ["a", "b"], to: ["out"] }],
      },
    ],
    links: [
      ["clock", "tick", "fast", "tick"],
      ["clock", "tick", "slow", "tick"],
      ["fast", "out", "merge", "a"],
      ["slow", "out", "merge", "b"],
    ],
  });
const forkCosts = (graph) =>
  measured(graph, {
    "fast.run": lat(1, 2, 3, 0.1),
    "slow.run": lat(0.5, 20, 40, 5),
  });

test("an and gate waits for the slowest branch, componentwise", () => {
  const graph = forkGraph("and");
  const solution = new T.ChainSolver(graph, forkCosts(graph)).solve(
    "clock.tick",
  );
  const join = solution.arrivals.get("merge.join");
  assert.equal(join.fold, "max");
  // min: fast branch is 1 vs slow 0.5 → the fast branch has the larger min.
  near(join.arrive.min, 1);
  near(join.arrive.mean, 5 + 20);
  near(join.arrive.max, 10 + 40);
  assert.equal(join.arrive.approx, true);
  const minChain = T.chainTo(solution, "merge.join", "min");
  const maxChain = T.chainTo(solution, "merge.join", "max");
  assert.ok(minChain.events.includes("fast.run"));
  assert.ok(maxChain.events.includes("slow.run"));
});

test("an or gate fires on the earliest branch", () => {
  const graph = forkGraph("or");
  const solution = new T.ChainSolver(graph, forkCosts(graph)).solve(
    "clock.tick",
  );
  const join = solution.arrivals.get("merge.join");
  assert.equal(join.fold, "min");
  near(join.arrive.min, 0.5);
  near(join.arrive.mean, 5 + 2);
  near(join.arrive.max, 10 + 3);
  assert.equal(join.branches.length, 2);
});

test("enumerate: one chain per or branch, ranked by max", () => {
  const graph = forkGraph("or");
  const solution = new T.ChainSolver(graph, forkCosts(graph)).solve(
    "clock.tick",
  );
  const { chains, total } = T.enumerateChains(solution, "merge.out.out");
  assert.equal(total, 2);
  assert.equal(chains.length, 2);
  assert.ok(chains[0].summary.max > chains[1].summary.max);
  assert.ok(chains[0].events.has("slow.run"));
  assert.ok(chains[1].events.has("fast.run"));
});

test("enumerate: an and gate keeps every branch in one chain", () => {
  const graph = forkGraph("and");
  const solution = new T.ChainSolver(graph, forkCosts(graph)).solve(
    "clock.tick",
  );
  const { chains, total } = T.enumerateChains(solution, "merge.out.out");
  assert.equal(total, 1);
  assert.ok(chains[0].events.has("slow.run"));
  assert.ok(chains[0].events.has("fast.run"));
  near(chains[0].summary.max, 50);
});

test("enumerate caps the list and reports the true count", () => {
  const graph = forkGraph("or");
  const solution = new T.ChainSolver(graph, forkCosts(graph)).solve(
    "clock.tick",
  );
  const { chains, total } = T.enumerateChains(solution, "merge.out.out", {
    limit: 1,
  });
  assert.equal(total, 2);
  assert.equal(chains.length, 1);
});

test("a cycle is cut once and the arrival stays finite", () => {
  const graph = build({
    nodes: [
      {
        name: "planner",
        inputs: ["state"],
        outputs: ["cmd"],
        processes: [
          {
            name: "plan",
            type: "periodic",
            frequency: 10,
            on: ["state"],
            to: ["cmd"],
          },
        ],
      },
      {
        name: "vehicle",
        inputs: ["cmd"],
        outputs: ["state"],
        processes: [
          { name: "drive", type: "on_input", on: ["cmd"], to: ["state"] },
        ],
      },
    ],
    links: [
      ["planner", "cmd", "vehicle", "cmd"],
      ["vehicle", "state", "planner", "state"],
    ],
  });
  const solution = new T.ChainSolver(graph, T.designCosts(graph)).solve(
    "planner.plan",
  );
  assert.equal(solution.loopEdges.size, 1);
  assert.equal(solution.reach.size, 6);
  const back = solution.arrivals.get("planner.in.state");
  assert.ok(Number.isFinite(back.total.max));
  near(back.total.max, 100);
  // the loop-closing edge is the one back into the source
  const [cut] = [...solution.loopEdges];
  assert.equal(graph.edgeById.get(cut).to, "planner.plan");
});

test("a once gate takes no part in a steady chain", () => {
  const graph = build({
    nodes: [
      {
        name: "src",
        outputs: ["a"],
        processes: [
          { name: "tick", type: "periodic", frequency: 1, to: ["a"] },
        ],
      },
      {
        name: "init",
        inputs: ["a"],
        outputs: ["b"],
        processes: [{ name: "setup", type: "once", on: ["a"], to: ["b"] }],
      },
    ],
    links: [["src", "a", "init", "a"]],
  });
  const solution = new T.ChainSolver(graph).solve("src.tick");
  assert.ok(!solution.reach.has("init.setup"));
  assert.ok(solution.reach.has("init.in.a"));
});

test("hop limit counts process gates", () => {
  const graph = forkGraph("or");
  const solution = new T.ChainSolver(graph).solve("clock.tick", {
    hopLimit: 1,
  });
  assert.ok(solution.reach.has("fast.run"));
  assert.ok(!solution.reach.has("merge.join"));
});

test("a gate without a declared type is folded as or and reported", () => {
  const graph = forkGraph(null);
  const solution = new T.ChainSolver(graph).solve("clock.tick");
  assert.deepEqual(solution.unknownGates, ["merge.join"]);
  assert.equal(solution.arrivals.get("merge.join").fold, "min");
});

test("measured costs fall back to the design per hop and name their source", () => {
  const graph = forkGraph("and");
  const partial = {
    exec: (event) =>
      event.id === "fast.run"
        ? T.fromRecord({ ...lat(2, 3, 9, 1), count: 500 }, "measured")
        : null,
    comm: (edge, from, to) =>
      to.id === "merge.in.b"
        ? T.fromRecord(lat(0.1, 0.2, 0.3, 0.05), "measured")
        : null,
  };
  const solution = new T.ChainSolver(
    graph,
    T.measuredCosts(graph, partial),
  ).solve("clock.tick");
  assert.equal(solution.arrivals.get("fast.run").exec.source, "measured");
  assert.equal(solution.arrivals.get("fast.run").exec.count, 500);
  assert.equal(solution.arrivals.get("slow.run").exec.source, "unmeasured");
  const [intoB] = solution.arrivals.get("merge.in.b").branches;
  assert.equal(intoB.comm.source, "measured");
  const [intoA] = solution.arrivals.get("merge.in.a").branches;
  assert.equal(intoA.comm.source, "none");
});

// ── Dead and unmeasured branches ────────────────────────────────────────────

test("a placeholder counts as unknown and a dead term makes a sum dead", () => {
  assert.equal(T.UNMEASURED.unknown, 1);
  assert.equal(T.add(T.UNMEASURED, T.UNMEASURED).unknown, 2);
  const gone = T.dead("process exited (code -6)");
  assert.equal(gone.source, "dead");
  const sum = T.add(T.summary({ min: 1, mean: 2, max: 3 }), gone);
  assert.equal(sum.dead, true);
  assert.equal(sum.reason, "process exited (code -6)");
  assert.equal(T.formatSummary(sum), "never ran");
});

test("fold: an or gate skips dead branches, an and gate waiting on one is dead", () => {
  const live = { key: "live", summary: T.summary({ min: 5, mean: 6, max: 7 }) };
  const gone = {
    key: "gone",
    summary: T.add(T.summary({ min: 1, mean: 1, max: 1 }), T.dead("never")),
  };
  const or = T.fold([gone, live], "min");
  assert.deepEqual(or.via, { min: "live", mean: "live", max: "live" });
  assert.equal(or.summary.dead, false);
  const and = T.fold([gone, live], "max");
  assert.equal(and.summary.dead, true);
  assert.equal(and.summary.reason, "never");
  // Every branch dead: the fold is dead and reads the branches as they are.
  const all = T.fold([gone], "min");
  assert.equal(all.summary.dead, true);
});

test("fold: a measured branch outranks one summing placeholders at either gate", () => {
  const measured = {
    key: "m",
    summary: T.summary({ min: 5, mean: 6, max: 7, source: "measured" }),
  };
  const guessed = { key: "g", summary: T.add(T.UNMEASURED, T.summary()) };
  const or = T.fold([guessed, measured], "min");
  assert.deepEqual(or.via, { min: "m", mean: "m", max: "m" });
  assert.equal(or.summary.unknown, 0);
  const and = T.fold([guessed, measured], "max");
  assert.deepEqual(and.via, { min: "m", mean: "m", max: "m" });
  assert.equal(and.summary.unknown, 1); // the gate still waits on a placeholder
  // An and gate keeps a placeholder branch that arrives last: its measured
  // links can put it on the critical path.
  const late = {
    key: "l",
    summary: T.add(T.UNMEASURED, T.summary({ min: 50, mean: 60, max: 80 })),
  };
  const critical = T.fold([late, measured], "max");
  assert.deepEqual(critical.via, { min: "l", mean: "l", max: "l" });
  assert.equal(critical.summary.max, 80);
  assert.equal(critical.summary.unknown, 1);
  assert.deepEqual(T.fold([late, measured], "min").via, {
    min: "m",
    mean: "m",
    max: "m",
  });
  // With placeholders only, the fold is what it always was.
  const both = T.fold([guessed, { key: "h", summary: T.UNMEASURED }], "min");
  assert.equal(both.summary.unknown, 1);
});

test("a dead branch of an or gate never carries the chain", () => {
  const graph = forkGraph("or");
  const costs = T.measuredCosts(graph, {
    exec: (event) => {
      if (event.id === "fast.run") return T.dead("output never published");
      if (event.id === "slow.run")
        return T.fromRecord(lat(20, 20, 20, 0), "measured");
      return null;
    },
    comm: () => null,
  });
  const solution = new T.ChainSolver(graph, costs).solve("clock.tick");
  const join = solution.arrivals.get("merge.join");
  assert.equal(join.fold, "min");
  assert.equal(join.arrive.dead, false);
  assert.ok(
    T.chainTo(solution, "merge.join", "mean").events.includes("slow.run"),
  );
  assert.equal(solution.arrivals.get("fast.run").total.dead, true);
});

// ── Peer sources ────────────────────────────────────────────────────────────

// Two lidars into one concatenation, an imu into each lidar's corrector as a
// side input, and a synchronizer downstream fed by the concatenated cloud
// twice (once straight, once through a segmenter).
const lidar = (name) => ({
  name,
  inputs: [{ name: "imu", type: "imu" }],
  outputs: ["cloud"],
  processes: [
    { name: "scan", type: "periodic", frequency: 10 },
    {
      name: "correct",
      type: "and",
      after: ["scan"],
      on: ["imu"],
      to: ["cloud"],
    },
  ],
});
const fanInGraph = ({ loop = false } = {}) =>
  build({
    nodes: [
      lidar("left"),
      lidar("right"),
      {
        name: "imu",
        outputs: [{ name: "data", type: "imu" }],
        processes: [
          { name: "sample", type: "periodic", frequency: 100, to: ["data"] },
        ],
      },
      {
        name: "concat",
        inputs: ["in1", "in2"],
        outputs: ["cloud"],
        processes: [
          { name: "update", type: "or", on: ["in1", "in2"], to: ["cloud"] },
        ],
      },
      {
        name: "segment",
        inputs: ["cloud"],
        outputs: ["cloud"],
        processes: [
          { name: "run", type: "on_input", on: ["cloud"], to: ["cloud"] },
        ],
      },
      {
        name: "sync",
        inputs: ["raw", "obstacle"],
        outputs: [{ name: "grid", type: "grid" }],
        processes: [
          { name: "fuse", type: "and", on: ["raw", "obstacle"], to: ["grid"] },
        ],
      },
      ...(loop
        ? [
            {
              name: "corrector",
              inputs: [{ name: "grid", type: "grid" }],
              outputs: [{ name: "data", type: "imu" }],
              processes: [
                { name: "run", type: "on_input", on: ["grid"], to: ["data"] },
              ],
            },
          ]
        : []),
    ],
    links: [
      ["left", "cloud", "concat", "in1"],
      ["right", "cloud", "concat", "in2"],
      ["imu", "data", "left", "imu"],
      ["imu", "data", "right", "imu"],
      ["concat", "cloud", "segment", "cloud"],
      ["concat", "cloud", "sync", "raw"],
      ["segment", "cloud", "sync", "obstacle"],
      ...(loop
        ? [
            ["sync", "grid", "corrector", "grid"],
            ["corrector", "data", "left", "imu"],
            ["corrector", "data", "right", "imu"],
          ]
        : []),
    ],
  });

test("a stream's roots are the clocks upstream on its message type", () => {
  const graph = fanInGraph();
  assert.deepEqual([...graph.streamRoots("concat.in.in1")], ["left.scan"]);
  assert.deepEqual([...graph.streamRoots("sync.in.obstacle")].sort(), [
    "left.scan",
    "right.scan",
  ]);
  // the imu feeds the corrector as a side input of another type
  assert.deepEqual([...graph.streamRoots("left.in.imu")], ["imu.sample"]);
});

test("clocks whose streams one gate merges are peers; side inputs and rejoined forks are not", () => {
  const graph = fanInGraph();
  const sets = graph.peerRoots();
  assert.deepEqual(
    sets.map((ids) => ids.sort()),
    [["imu.sample"], ["left.scan", "right.scan"]],
  );
});

test("peer sources are solved together and meet at the merging gate", () => {
  const graph = fanInGraph();
  // the left lidar is faster at best and slower at worst than the right one
  const costs = measured(graph, {
    "left.correct": lat(1, 3, 20, 0),
    "right.correct": lat(5, 5, 5, 0),
    "concat.update": lat(2, 2, 2, 0),
  });
  const solution = new T.ChainSolver(graph, costs).solve([
    "left.scan",
    "right.scan",
  ]);
  assert.deepEqual([...solution.sourceIds], ["left.scan", "right.scan"]);
  assert.equal(solution.arrivals.get("right.scan").rank, 0);
  const update = solution.arrivals.get("concat.update");
  assert.equal(update.fold, "min");
  assert.deepEqual(update.branches.map((b) => b.fromId).sort(), [
    "concat.in.in1",
    "concat.in.in2",
  ]);
  // both lidars sample uniformly over 100 ms; the or gate takes the earlier
  // arrival component by component
  near(update.arrive.min, 1);
  near(update.arrive.max, 105);
  assert.equal(update.via.min, graph.edgeId("concat.in.in1", "concat.update"));
  assert.equal(update.via.max, graph.edgeId("concat.in.in2", "concat.update"));
  const min = T.chainTo(solution, "concat.out.cloud", "min");
  const max = T.chainTo(solution, "concat.out.cloud", "max");
  assert.equal(min.events[0], "left.scan");
  assert.equal(max.events[0], "right.scan");
  assert.equal(T.countChains(solution, "concat.out.cloud"), 2);
  assert.equal(
    T.enumerateChains(solution, "concat.out.cloud").chains.length,
    2,
  );
});

test("a loop is cut where the long way round rejoins a source's own chain", () => {
  const graph = fanInGraph({ loop: true });
  const solution = new T.ChainSolver(graph, T.designCosts(graph)).solve([
    "left.scan",
    "right.scan",
  ]);
  // every lidar keeps its short way into the merge; the corrector's feedback
  // reaches the lidars' imu inputs and is cut there, into the gates
  const cut = [...solution.loopEdges].map((id) => graph.edgeById.get(id));
  assert.deepEqual(cut.map((edge) => edge.to).sort(), [
    "left.correct",
    "right.correct",
  ]);
  assert.deepEqual(cut.map((edge) => edge.from).sort(), [
    "left.in.imu",
    "right.in.imu",
  ]);
  const update = solution.arrivals.get("concat.update");
  assert.equal(update.branches.length, 2);
  assert.ok(
    solution.order.indexOf("left.correct") <
      solution.order.indexOf("concat.update"),
  );
  assert.ok(
    solution.order.indexOf("right.correct") <
      solution.order.indexOf("concat.update"),
  );
});

test("a single source still reads its chain outward and cuts the loop coming back", () => {
  const graph = fanInGraph({ loop: true });
  const solution = new T.ChainSolver(graph, T.designCosts(graph)).solve(
    "left.scan",
  );
  const cutInto = [...solution.loopEdges].map(
    (id) => graph.edgeById.get(id).to,
  );
  // the feedback into this lidar is cut; the other lidar is reached only
  // through the loop, so its way into the merge is the long way round
  assert.deepEqual(cutInto.sort(), ["concat.update", "left.correct"]);
  assert.ok(solution.reach.has("right.correct"));
  assert.equal(solution.arrivals.get("concat.update").branches.length, 1);
});

test("a chain end is a loop where its edge was cut, open where nothing follows", () => {
  const graph = build({
    nodes: [
      {
        name: "planner",
        inputs: ["state"],
        outputs: ["cmd", "debug"],
        processes: [
          {
            name: "plan",
            type: "periodic",
            frequency: 10,
            on: ["state"],
            to: ["cmd", "debug"],
          },
        ],
      },
      {
        name: "vehicle",
        inputs: ["cmd"],
        outputs: ["state"],
        processes: [
          { name: "drive", type: "on_input", on: ["cmd"], to: ["state"] },
        ],
      },
    ],
    links: [
      ["planner", "cmd", "vehicle", "cmd"],
      ["vehicle", "state", "planner", "state"],
    ],
  });
  const solution = new T.ChainSolver(graph, T.designCosts(graph)).solve(
    "planner.plan",
  );
  assert.deepEqual(solution.sinkIds.sort(), [
    "planner.in.state",
    "planner.out.debug",
  ]);
  const back = T.endOf(solution, graph, "planner.in.state");
  assert.equal(back.kind, "loop");
  assert.deepEqual(back.rejoins, ["planner.plan"]);
  const exits = T.loopExits(solution, graph, "planner.in.state");
  assert.equal(exits.length, 1);
  assert.equal(exits[0].toId, "planner.plan");
  assert.ok(solution.loopEdges.has(exits[0].edgeId));
  assert.deepEqual(T.endOf(solution, graph, "planner.out.debug"), {
    kind: "open",
    rejoins: [],
  });
  // a kept edge is no loop exit, whatever follows it
  assert.equal(T.loopExits(solution, graph, "planner.out.cmd").length, 0);
});

test("a chain cut by the hop limit ends at the limit, not open", () => {
  const graph = build({
    nodes: [
      {
        name: "a",
        outputs: ["x"],
        processes: [
          { name: "tick", type: "periodic", frequency: 10, to: ["x"] },
        ],
      },
      {
        name: "b",
        inputs: ["x"],
        outputs: ["y"],
        processes: [{ name: "relay", type: "on_input", on: ["x"], to: ["y"] }],
      },
      {
        name: "c",
        inputs: ["y"],
        processes: [{ name: "sink", type: "on_input", on: ["y"] }],
      },
    ],
    links: [
      ["a", "x", "b", "x"],
      ["b", "y", "c", "y"],
    ],
  });
  const solver = new T.ChainSolver(graph, T.designCosts(graph));
  const limited = solver.solve("a.tick", { hopLimit: 1 });
  assert.deepEqual(limited.sinkIds, ["c.in.y"]);
  assert.equal(T.endOf(limited, graph, "c.in.y").kind, "limit");
  const full = solver.solve("a.tick");
  assert.deepEqual(full.sinkIds, ["c.sink"]);
  assert.equal(T.endOf(full, graph, "c.sink").kind, "open");
});
