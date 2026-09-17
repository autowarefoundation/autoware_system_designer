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

// nodes: [{ name, inputs: [name], outputs: [name], processes: [{ name, type,
// frequency, on: [input names], to: [output names], after: [process names],
// latency }] }]; links: [[nodeA, output, nodeB, input]].
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

  const children = nodes.map((node) => {
    const inPorts = (node.inputs || []).map((name) => ({
      name,
      topic: [`${node.name}_${name}`],
      event: event(`${node.name}.in.${name}`, `input_${name}`, "on_input"),
    }));
    const outPorts = (node.outputs || []).map((name) => ({
      name,
      topic: [`${node.name}_${name}`],
      event: event(`${node.name}.out.${name}`, `output_${name}`, "to_output"),
    }));
    const processes = (node.processes || []).map((p) => {
      const e = event(`${node.name}.${p.name}`, p.name, p.type ?? null);
      if (p.frequency !== undefined) e.frequency = p.frequency;
      if (p.latency) e.latency = p.latency;
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

test("compare flags a max overrun and a grown spread", () => {
  const declared = T.summary({ min: 1, mean: 2, max: 5, sd: 1 });
  const measured = T.summary({ min: 1, mean: 3, max: 8, sd: 3 });
  const delta = T.compare(declared, measured);
  assert.equal(delta.deltaMax, 3);
  assert.equal(delta.maxExceeded, true);
  assert.equal(delta.sdGrew, true);
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
            latency: lat(1, 2, 4, 0.5),
          },
        ],
      },
    ],
    links: [["sensor", "cloud", "filter", "cloud"]],
  });

test("a two-hop chain: sampling delay plus declared execution", () => {
  const graph = chainGraph();
  const solver = new T.ChainSolver(graph, T.declaredCosts(graph));
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
  assert.equal(hops[3].exec.source, "declared");
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
            latency: lat(1, 2, 3, 0.1),
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
            latency: lat(0.5, 20, 40, 5),
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

test("an and gate waits for the slowest branch, componentwise", () => {
  const graph = forkGraph("and");
  const solution = new T.ChainSolver(graph, T.declaredCosts(graph)).solve(
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
  const solution = new T.ChainSolver(graph, T.declaredCosts(graph)).solve(
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
  const solution = new T.ChainSolver(graph, T.declaredCosts(graph)).solve(
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
  const solution = new T.ChainSolver(graph, T.declaredCosts(graph)).solve(
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
  const solution = new T.ChainSolver(graph, T.declaredCosts(graph)).solve(
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
  const solution = new T.ChainSolver(graph, T.declaredCosts(graph)).solve(
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

test("measured costs fall back to declared per hop and name their source", () => {
  const graph = forkGraph("and");
  const measured = {
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
    T.measuredCosts(graph, measured),
  ).solve("clock.tick");
  assert.equal(solution.arrivals.get("fast.run").exec.source, "measured");
  assert.equal(solution.arrivals.get("fast.run").exec.count, 500);
  assert.equal(solution.arrivals.get("slow.run").exec.source, "declared");
  const [intoB] = solution.arrivals.get("merge.in.b").branches;
  assert.equal(intoB.comm.source, "measured");
  const [intoA] = solution.arrivals.get("merge.in.a").branches;
  assert.equal(intoA.comm.source, "none");
});
