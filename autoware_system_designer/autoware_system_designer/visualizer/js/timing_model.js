// Timing Model Module
// Latency of an event chain as distribution summaries. Every cost and every
// arrival is { min, mean, max, sd }; hops add, gates fold, and one solve over
// the event graph yields the minimum, mean and maximum chain at once. Free of
// the DOM, so the same module runs under node for the unit tests.

(function () {
  const SQRT12 = Math.sqrt(12);

  // ── Summaries ───────────────────────────────────────────────────────────────

  // sd is composed only over hops that know theirs; missingSd counts the rest.
  // approx marks a fold whose mean and sd describe one branch, not the set.
  // source names where the numbers came from: measured, derived, unmeasured,
  // dead, none. unknown counts the placeholders summed into the value; dead
  // marks a value behind a gate the run never fired, with the reason.
  function summary(fields = {}) {
    const sd = fields.sd === undefined ? 0 : fields.sd;
    return {
      min: fields.min ?? 0,
      mean: fields.mean ?? 0,
      max: fields.max ?? 0,
      sd: sd === null ? 0 : sd,
      count: fields.count ?? null,
      missingSd: fields.missingSd ?? (sd === null ? 1 : 0),
      approx: Boolean(fields.approx),
      source: fields.source || "none",
      unknown: fields.unknown ?? 0,
      dead: Boolean(fields.dead),
      reason: fields.reason ?? null,
    };
  }

  const ZERO = Object.freeze(summary());

  // Placeholder for a process run no measurement covers yet: zero width,
  // marked so the view can show the gap.
  const UNMEASURED = Object.freeze(
    summary({ source: "unmeasured", unknown: 1 }),
  );

  // A process run the record says never happened: nothing downstream of it
  // arrives, and a fold never picks it while a live branch exists.
  function dead(reason) {
    return summary({ source: "dead", dead: true, reason });
  }

  // Sampling delay of a clock: uniform over one period.
  function uniform(lo, hi, source = "derived") {
    return summary({
      min: lo,
      mean: (lo + hi) / 2,
      max: hi,
      sd: (hi - lo) / SQRT12,
      source,
    });
  }

  // A measured record: min_ms / mean_ms / max_ms, sd_ms optional.
  function fromRecord(record, source) {
    if (!record || record.min_ms === undefined || record.max_ms === undefined) {
      return null;
    }
    const mean = record.mean_ms ?? (record.min_ms + record.max_ms) / 2;
    return summary({
      min: record.min_ms,
      mean,
      max: record.max_ms,
      sd:
        record.sd_ms === undefined || record.sd_ms === null
          ? null
          : record.sd_ms,
      count: record.count ?? null,
      source,
    });
  }

  // Series composition: components add, variances add, placeholders count up
  // and a dead term makes the sum dead.
  function add(a, b) {
    return summary({
      min: a.min + b.min,
      mean: a.mean + b.mean,
      max: a.max + b.max,
      sd: Math.sqrt(a.sd * a.sd + b.sd * b.sd),
      count: minCount(a.count, b.count),
      missingSd: a.missingSd + b.missingSd,
      approx: a.approx || b.approx,
      source: "none",
      unknown: a.unknown + b.unknown,
      dead: a.dead || b.dead,
      reason: a.reason ?? b.reason,
    });
  }

  function minCount(a, b) {
    if (a === null) return b;
    if (b === null) return a;
    return Math.min(a, b);
  }

  // Componentwise fold over branches [{ key, summary }]: max for a gate that
  // waits for every trigger, min for one that fires on any. sd and count are
  // borrowed from the branch that dominates the mean. A dead branch never
  // delivers: an or gate folds the live branches, an and gate waiting on one
  // is dead itself. At an or gate, live branches fully measured take
  // precedence over ones summing placeholders, whose zero width would
  // otherwise always be the earliest; an and gate waits for every branch,
  // placeholders included, and stays marked unknown while one is in the pool.
  function fold(branches, pick) {
    if (!branches.length) return { summary: summary(), via: emptyVia() };
    const alive = branches.filter((b) => !b.summary.dead);
    const isDead =
      pick === "max" ? alive.length < branches.length : !alive.length;
    let pool = alive.length ? alive : branches;
    if (pick === "min") {
      const known = pool.filter((b) => !b.summary.unknown);
      if (known.length) pool = known;
    }
    const better = pick === "max" ? (a, b) => a > b : (a, b) => a < b;
    const best = (component) =>
      pool.reduce((chosen, branch) =>
        better(branch.summary[component], chosen.summary[component])
          ? branch
          : chosen,
      );
    const byMin = best("min");
    const byMean = best("mean");
    const byMax = best("max");
    const reason = isDead
      ? (branches.find((b) => b.summary.dead)?.summary.reason ?? null)
      : null;
    return {
      summary: summary({
        min: byMin.summary.min,
        mean: byMean.summary.mean,
        max: byMax.summary.max,
        sd: byMean.summary.sd,
        count: byMean.summary.count,
        missingSd: byMean.summary.missingSd,
        approx: branches.length > 1 || branches.some((b) => b.summary.approx),
        unknown:
          pick === "max"
            ? Math.max(...pool.map((b) => b.summary.unknown))
            : byMean.summary.unknown,
        dead: isDead,
        reason,
      }),
      via: { min: byMin.key, mean: byMean.key, max: byMax.key },
    };
  }

  function emptyVia() {
    return { min: null, mean: null, max: null };
  }

  // The number a summary contributes to an axis: one component, or the mean
  // plus k standard deviations.
  function at(s, driver) {
    if (driver === "min" || driver === "mean" || driver === "max") {
      return s[driver];
    }
    const k = Number(String(driver).replace("sigma", "")) || 0;
    return s.mean + k * s.sd;
  }

  function formatMs(value, digits = 2) {
    if (value === null || value === undefined || Number.isNaN(value))
      return "—";
    const abs = Math.abs(value);
    const d = abs >= 100 ? 0 : abs >= 10 ? 1 : digits;
    return `${Number(value.toFixed(d))} ms`;
  }

  // "min / mean ± sd / max", with the marks the honesty rules ask for.
  function formatSummary(s) {
    if (!s) return "—";
    if (s.dead) return "never ran";
    const mean = `${formatMs(s.mean)}${s.approx ? "≈" : ""}`;
    const sd = s.missingSd ? `±${formatMs(s.sd)}?` : `±${formatMs(s.sd)}`;
    return `${formatMs(s.min)} / ${mean} ${sd} / ${formatMs(s.max)}`;
  }

  // ── Cost providers ──────────────────────────────────────────────────────────

  // Costs land on three places: the wait at a gate before it runs, the run of
  // a process gate, and the transport from an output to the input it feeds.
  // The design provider knows only what the design declares: a periodic
  // gate's sampling delay from its rate. Execution is a measured quantity, so
  // every process run is the unmeasured placeholder; transport is zero.
  function designCosts(graph) {
    return {
      wait(event) {
        if (event.type === "periodic" && event.frequency > 0) {
          return uniform(0, 1000 / event.frequency);
        }
        return ZERO;
      },
      exec(event) {
        return event.kind === "process" ? UNMEASURED : ZERO;
      },
      comm() {
        return ZERO;
      },
    };
  }

  // No time at all: the chain is read by rank.
  function logicalCosts() {
    return { wait: () => ZERO, exec: () => ZERO, comm: () => ZERO };
  }

  // The wait a message spent before the run that answered it: the node's
  // measured in→out response less the run. Components are taken so that the
  // series wait + run reproduces the response in min, mean and max; the
  // spread is what the response has beyond the run's.
  function sampling(response, run) {
    const min = Math.max(0, response.min - run.min);
    const max = Math.max(min, response.max - run.max);
    const mean = Math.min(Math.max(response.mean - run.mean, min), max);
    const variance = response.sd * response.sd - run.sd * run.sd;
    return summary({
      min,
      mean,
      max,
      sd: Math.sqrt(Math.max(0, variance)),
      count: response.count,
      missingSd: response.missingSd,
      source: "derived",
    });
  }

  // Measured costs where a sample exists, the design's where it does not.
  // `measured` answers wait(event), exec(event) and comm(edge, from, to) with a
  // summary or null; each returned summary names its source so a hop can be
  // marked.
  function measuredCosts(graph, measured) {
    const design = designCosts(graph);
    return {
      wait(event) {
        return measured.wait?.(event) || design.wait(event);
      },
      exec(event) {
        return measured.exec?.(event) || design.exec(event);
      },
      comm(edge, from, to) {
        return measured.comm?.(edge, from, to) || design.comm(edge, from, to);
      },
    };
  }

  // ── Solver ──────────────────────────────────────────────────────────────────

  // Gates of this type fire once at start-up and take no part in a steady chain.
  const EXCLUDED_TYPES = new Set(["once"]);

  // How a gate combines its triggers: `and` waits for every one, anything else
  // fires on the first; a gate with no declared type is folded as `or` and
  // reported.
  function foldOf(event) {
    if (event.kind === "process" && event.type === "and") return "max";
    return "min";
  }

  class ChainSolver {
    constructor(graph, costs) {
      this.graph = graph;
      this.costs = costs || designCosts(graph);
    }

    // Arrival summaries for everything the sources reach. Several sources fire
    // together at time zero: peer clocks whose streams one gate merges, so the
    // fold at that gate weighs one chain against the other. The event graph is
    // cyclic, so edges are kept in order of distance from the sources and an
    // edge that closes a cycle is cut where the long way round rejoins; what
    // remains is a DAG, and arrivals are folded along it in topological order.
    solve(sources, { hopLimit = null } = {}) {
      const graph = this.graph;
      const sourceIds = new Set([].concat(sources));
      sourceIds.forEach((id) => {
        if (!graph.events.get(id))
          throw new Error(`unknown source event ${id}`);
      });
      const [sourceId] = sourceIds;

      const reach = this._reach(sourceIds, hopLimit);
      const { order, loopEdges } = this._cut(sourceIds, reach);

      const arrivals = new Map();
      const edgeEnds = new Map(); // kept edge id → { fromId, toId }
      const unknownGates = [];
      order.forEach((id) => {
        const event = graph.events.get(id);
        const branches = [];
        (graph.pred.get(id) || []).forEach((fromId) => {
          if (!reach.has(fromId) || sourceIds.has(id)) return;
          const edgeId = graph.edgeId(fromId, id);
          if (loopEdges.has(edgeId)) return;
          const from = graph.events.get(fromId);
          const comm = this.costs.comm(graph.edgeById.get(edgeId), from, event);
          edgeEnds.set(edgeId, { fromId, toId: id });
          branches.push({
            key: edgeId,
            fromId,
            comm,
            summary: add(arrivals.get(fromId).total, comm),
          });
        });

        const mode = foldOf(event);
        if (event.kind === "process" && !event.type) unknownGates.push(id);
        const folded = sourceIds.has(id)
          ? { summary: summary(), via: emptyVia() }
          : fold(branches, mode);
        const wait = this.costs.wait(event);
        const exec = this.costs.exec(event);
        const start = add(folded.summary, wait);
        arrivals.set(id, {
          id,
          mode,
          fold: branches.length > 1 ? mode : null,
          branches,
          via: folded.via,
          arrive: folded.summary,
          wait,
          exec,
          start,
          total: add(start, exec),
          rank: 0,
        });
      });

      // Rank: longest gate count from a source along kept edges, for the view
      // that has no time to place events by.
      order.forEach((id) => {
        const arrival = arrivals.get(id);
        const event = graph.events.get(id);
        const step = event.kind === "process" ? 1 : 0;
        arrival.rank = arrival.branches.reduce(
          (rank, branch) =>
            Math.max(rank, arrivals.get(branch.fromId).rank + step),
          sourceIds.has(id) ? 0 : step,
        );
      });

      return {
        sourceId,
        sourceIds,
        reach,
        order,
        arrivals,
        edgeEnds,
        loopEdges,
        unknownGates,
        sinkIds: order.filter(
          (id) =>
            !(graph.succ.get(id) || []).some(
              (next) =>
                reach.has(next) && !loopEdges.has(graph.edgeId(id, next)),
            ),
        ),
      };
    }

    // Events the sources reach, start-up gates left out, within the hop limit
    // counted in process gates.
    _reach(sourceIds, hopLimit) {
      const graph = this.graph;
      const hops = new Map([...sourceIds].map((id) => [id, 0]));
      const queue = [...sourceIds];
      for (let cursor = 0; cursor < queue.length; cursor += 1) {
        const id = queue[cursor];
        (graph.succ.get(id) || []).forEach((nextId) => {
          const next = graph.events.get(nextId);
          if (!next || hops.has(nextId)) return;
          if (EXCLUDED_TYPES.has(next.type)) return;
          const hop = hops.get(id) + (next.kind === "process" ? 1 : 0);
          if (hopLimit !== null && hop > hopLimit) return;
          hops.set(nextId, hop);
          queue.push(nextId);
        });
      }
      return new Set(hops.keys());
    }

    // Cuts the reach into a DAG. Events are ranked by breadth-first distance
    // from the sources and edges are decided in that order: an edge that moves
    // away from the sources is kept; one that does not is kept only if the
    // kept edges do not already lead from its head back to its tail. Every
    // cycle has an edge leaving its farthest event, and that edge is decided
    // last, so it is the one cut: a chain is read outward from its sources
    // and a loop is cut where the long way round rejoins it. Returns a
    // topological order of the kept edges.
    _cut(sourceIds, reach) {
      const graph = this.graph;
      const distance = new Map([...sourceIds].map((id) => [id, 0]));
      const byDistance = [...sourceIds];
      for (let cursor = 0; cursor < byDistance.length; cursor += 1) {
        const id = byDistance[cursor];
        (graph.succ.get(id) || []).forEach((nextId) => {
          if (!reach.has(nextId) || distance.has(nextId)) return;
          distance.set(nextId, distance.get(id) + 1);
          byDistance.push(nextId);
        });
      }

      const kept = new Map(byDistance.map((id) => [id, []]));
      const leadsBack = (fromId, toId) => {
        const stack = [fromId];
        const seen = new Set([fromId]);
        while (stack.length) {
          const id = stack.pop();
          if (id === toId) return true;
          (kept.get(id) || []).forEach((nextId) => {
            if (!seen.has(nextId)) {
              seen.add(nextId);
              stack.push(nextId);
            }
          });
        }
        return false;
      };

      const loopEdges = new Set();
      const indegree = new Map(byDistance.map((id) => [id, 0]));
      byDistance.forEach((id) => {
        (graph.succ.get(id) || []).forEach((nextId) => {
          if (!reach.has(nextId)) return;
          const forward = distance.get(nextId) > distance.get(id);
          if (forward || !leadsBack(nextId, id)) {
            kept.get(id).push(nextId);
            indegree.set(nextId, indegree.get(nextId) + 1);
          } else {
            loopEdges.add(graph.edgeId(id, nextId));
          }
        });
      });

      const order = [];
      const ready = byDistance.filter((id) => indegree.get(id) === 0);
      for (let cursor = 0; cursor < ready.length; cursor += 1) {
        const id = ready[cursor];
        order.push(id);
        kept.get(id).forEach((nextId) => {
          indegree.set(nextId, indegree.get(nextId) - 1);
          if (indegree.get(nextId) === 0) ready.push(nextId);
        });
      }
      return { order, loopEdges };
    }
  }

  // ── Chains ──────────────────────────────────────────────────────────────────

  // The chain one component of the solution took to a sink: the events from
  // the source and the edges between them, read back through `via`.
  function chainTo(solution, sinkId, component = "max") {
    const events = [];
    const edges = [];
    const seen = new Set();
    let id = sinkId;
    while (id && !seen.has(id)) {
      seen.add(id);
      events.push(id);
      const arrival = solution.arrivals.get(id);
      if (!arrival) break;
      const edgeId = arrival.via[component];
      if (!edgeId) break;
      const branch = arrival.branches.find((b) => b.key === edgeId);
      if (!branch) break;
      edges.push(edgeId);
      id = branch.fromId;
    }
    events.reverse();
    edges.reverse();
    return {
      component,
      events,
      edges,
      total: solution.arrivals.get(sinkId)?.total || null,
    };
  }

  // Per-hop table for a chain: what each event waited, ran and rode in on.
  function hopsOf(solution, chain) {
    return chain.events.map((id, index) => {
      const arrival = solution.arrivals.get(id);
      const edgeId = index ? chain.edges[index - 1] : null;
      const branch = edgeId
        ? arrival.branches.find((b) => b.key === edgeId)
        : null;
      return {
        id,
        edgeId,
        comm: branch ? branch.comm : null,
        wait: arrival.wait,
        exec: arrival.exec,
        start: arrival.start,
        total: arrival.total,
        fold: arrival.fold,
        branches: arrival.branches,
      };
    });
  }

  // Every distinct chain to a sink: one branch chosen per `or` gate, every
  // branch kept at an `and`. The count is the product over the gates, so the
  // list is ranked by max and capped; `total` says how many there were.
  function enumerateChains(solution, sinkId, { limit = 20 } = {}) {
    const memo = new Map();
    const sources = solution.sourceIds;

    const expand = (id) => {
      if (memo.has(id)) return memo.get(id);
      const arrival = solution.arrivals.get(id);
      const own = add(arrival.wait, arrival.exec);
      let alternatives;
      if (sources.has(id) || !arrival.branches.length) {
        alternatives = [{ summary: own, edges: new Set(), count: 1 }];
      } else {
        const perBranch = arrival.branches.map((branch) =>
          expand(branch.fromId).map((alt) => ({
            summary: add(alt.summary, branch.comm),
            edges: new Set([...alt.edges, branch.key]),
            count: alt.count,
          })),
        );
        if (arrival.mode === "max") {
          alternatives = product(perBranch, limit).map((combo) => ({
            summary: fold(
              combo.map((alt) => ({ key: null, summary: alt.summary })),
              "max",
            ).summary,
            edges: new Set(combo.flatMap((alt) => [...alt.edges])),
            count: combo.reduce((n, alt) => n * alt.count, 1),
          }));
        } else {
          alternatives = perBranch.flat();
        }
        alternatives = alternatives.map((alt) => ({
          ...alt,
          summary: add(alt.summary, own),
        }));
        alternatives = cap(alternatives, limit);
      }
      memo.set(id, alternatives);
      return alternatives;
    };

    const total = countChains(solution, sinkId);
    const chains = cap(expand(sinkId), limit).map((alt) => ({
      summary: alt.summary,
      edges: alt.edges,
      events: eventsOf(solution, alt.edges, sinkId),
    }));
    return { chains, total, limit };
  }

  // Cartesian product of the per-branch alternatives, beam-capped by max.
  function product(lists, limit) {
    let combos = [[]];
    lists.forEach((list) => {
      const next = [];
      combos.forEach((combo) =>
        list.forEach((alt) => next.push([...combo, alt])),
      );
      combos = next
        .sort(
          (a, b) =>
            Math.max(...b.map((x) => x.summary.max)) -
            Math.max(...a.map((x) => x.summary.max)),
        )
        .slice(0, Math.max(limit, 1));
    });
    return combos;
  }

  function cap(alternatives, limit) {
    return [...alternatives]
      .sort((a, b) => b.summary.max - a.summary.max)
      .slice(0, Math.max(limit, 1));
  }

  // Number of distinct chains without materializing them, saturating high.
  function countChains(solution, sinkId) {
    const memo = new Map();
    const CAP = 1e9;
    const count = (id) => {
      if (memo.has(id)) return memo.get(id);
      const arrival = solution.arrivals.get(id);
      let n = 1;
      if (!solution.sourceIds.has(id) && arrival.branches.length) {
        const per = arrival.branches.map((b) => count(b.fromId));
        n =
          arrival.mode === "max"
            ? per.reduce((acc, k) => Math.min(CAP, acc * k), 1)
            : per.reduce((acc, k) => Math.min(CAP, acc + k), 0);
      }
      memo.set(id, n);
      return n;
    };
    return count(sinkId);
  }

  function eventsOf(solution, edges, sinkId) {
    const events = new Set([sinkId]);
    edges.forEach((edgeId) => {
      const ends = solution.edgeEnds.get(edgeId);
      if (!ends) return;
      events.add(ends.fromId);
      events.add(ends.toId);
    });
    return events;
  }

  // ── Chain ends ──────────────────────────────────────────────────────────────

  // The loop-closing edges leaving an event, each with the event it rejoins.
  function loopExits(solution, graph, eventId) {
    return (graph.succ.get(eventId) || [])
      .map((toId) => ({
        edgeId: graph.edgeId(eventId, toId),
        fromId: eventId,
        toId,
      }))
      .filter(({ edgeId }) => edgeId && solution.loopEdges.has(edgeId));
  }

  // How a chain stops at an event. `loop`: an edge leaving it was cut as
  // loop-closing, the chain rejoins itself. `open`: nothing in the graph
  // follows it. `limit`: its successors lie beyond the hop limit.
  function endOf(solution, graph, eventId) {
    const loops = loopExits(solution, graph, eventId);
    if (loops.length) {
      return { kind: "loop", rejoins: loops.map((exit) => exit.toId) };
    }
    const beyond = (graph.succ.get(eventId) || []).some((id) => {
      const next = graph.events.get(id);
      return next && !EXCLUDED_TYPES.has(next.type);
    });
    return { kind: beyond ? "limit" : "open", rejoins: [] };
  }

  const TimingModel = {
    ZERO,
    UNMEASURED,
    summary,
    dead,
    uniform,
    fromRecord,
    sampling,
    add,
    fold,
    at,
    formatMs,
    formatSummary,
    foldOf,
    logicalCosts,
    designCosts,
    measuredCosts,
    ChainSolver,
    chainTo,
    hopsOf,
    enumerateChains,
    countChains,
    loopExits,
    endOf,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = TimingModel;
  } else {
    window.TimingModel = TimingModel;
  }
})();
