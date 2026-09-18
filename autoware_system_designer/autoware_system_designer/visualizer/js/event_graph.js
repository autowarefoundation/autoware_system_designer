// Event Graph Module
// The trigger graph of an exported system: every port, process and queue event
// is a vertex, every trigger relation an edge. A queue read is a second, loose
// relation kept beside the edges: the reader takes what the queue holds when
// its own trigger fires, so no chain, rate or wait runs through it. Built once
// from the instance tree and read by the diagrams that draw chains over it.
// Free of the DOM, so the same module runs under node for the solver tests.

(function () {
  // Frequency is propagated from clock roots only, so these types start a chain.
  const CLOCK_TYPES = new Set(["periodic", "once"]);

  class EventGraph {
    static CLOCK_TYPES = CLOCK_TYPES;

    constructor() {
      this.events = new Map(); // eventId → event record
      this.instances = new Map(); // instanceId → { data, depth }
      this.instanceByPath = new Map(); // instance path → instance
      this.succ = new Map(); // eventId → [eventId] it triggers
      this.pred = new Map(); // eventId → [eventId] triggering it
      this.edgeList = []; // { id, from, to, cross }
      this.edgeById = new Map();
      this.edgeIdByKey = new Map(); // "from>to" → edgeId
      this.clocksOf = new Map(); // eventId → Set(clock root id)
      this.clockRootIds = [];
      this.activeIds = new Set(); // events carrying at least one relation
      this.chainEndIds = new Set(); // events the chain stops at
      this.readsOf = new Map(); // reader eventId → [queue eventId]
      this.readersOf = new Map(); // queue eventId → [reader eventId]
      this.readEdges = []; // { id, from: queue, to: reader }
      this.readEdgeById = new Map();
    }

    // ── Construction ────────────────────────────────────────────────────────────

    build(root) {
      this.events.clear();
      this.instances.clear();
      this.instanceByPath.clear();
      this.succ.clear();
      this.pred.clear();
      this.edgeById.clear();
      this.edgeIdByKey.clear();
      this.edgeList = [];
      this.activeIds.clear();
      this.readsOf.clear();
      this.readersOf.clear();
      this.readEdges = [];
      this.readEdgeById.clear();

      const addEvent = (event, instance, kind, port) => {
        if (!event?.unique_id || this.events.has(String(event.unique_id))) {
          return;
        }
        this.events.set(String(event.unique_id), {
          id: String(event.unique_id),
          name: event.name || "event",
          type: event.type || null,
          kind,
          ownerId: String(instance.unique_id),
          port: port || null,
          frequency: event.frequency ?? null,
          warn_rate: event.warn_rate ?? null,
          error_rate: event.error_rate ?? null,
          timeout: event.timeout ?? null,
          triggers: (event.trigger_ids || []).map(String),
          actions: (event.action_ids || []).map(String),
          reads: (event.read_ids || []).map(String),
          readers: (event.reader_ids || []).map(String),
        });
      };

      const visit = (instance, depth) => {
        if (!instance?.unique_id) return;
        this.instances.set(String(instance.unique_id), {
          data: instance,
          depth,
        });
        if (instance.path) this.instanceByPath.set(instance.path, instance);
        (instance.in_ports || []).forEach((port) =>
          addEvent(port.event, instance, "input", port),
        );
        (instance.out_ports || []).forEach((port) =>
          addEvent(port.event, instance, "output", port),
        );
        (instance.events || []).forEach((event) =>
          addEvent(
            event,
            instance,
            event.type === "queue" ? "queue" : "process",
            null,
          ),
        );
        (instance.children || []).forEach((child) => visit(child, depth + 1));
      };
      visit(root, 0);

      // trigger_ids and action_ids are the same relation read from either end.
      const link = (fromId, toId) => {
        if (fromId === toId) return;
        const from = this.events.get(fromId);
        const to = this.events.get(toId);
        if (!from || !to) return;
        const key = `${fromId}>${toId}`;
        if (this.edgeIdByKey.has(key)) return;

        const id = `le_${this.edgeList.length}`;
        const edge = {
          id,
          from: fromId,
          to: toId,
          cross: from.ownerId !== to.ownerId,
        };
        this.edgeIdByKey.set(key, id);
        this.edgeById.set(id, edge);
        this.edgeList.push(edge);
        if (!this.succ.has(fromId)) this.succ.set(fromId, []);
        this.succ.get(fromId).push(toId);
        if (!this.pred.has(toId)) this.pred.set(toId, []);
        this.pred.get(toId).push(fromId);
      };

      this.events.forEach((event) => {
        event.triggers.forEach((triggerId) => link(triggerId, event.id));
        event.actions.forEach((actionId) => link(event.id, actionId));
      });

      // read_ids and reader_ids are one relation read from either end.
      const read = (queueId, readerId) => {
        if (queueId === readerId) return;
        if (!this.events.has(queueId) || !this.events.has(readerId)) return;
        const readers = this.readersOf.get(queueId) || [];
        if (readers.includes(readerId)) return;
        readers.push(readerId);
        this.readersOf.set(queueId, readers);
        if (!this.readsOf.has(readerId)) this.readsOf.set(readerId, []);
        this.readsOf.get(readerId).push(queueId);
        const edge = {
          id: `re_${this.readEdges.length}`,
          from: queueId,
          to: readerId,
        };
        this.readEdges.push(edge);
        this.readEdgeById.set(edge.id, edge);
      };
      this.events.forEach((event) => {
        event.reads.forEach((queueId) => read(queueId, event.id));
        event.readers.forEach((readerId) => read(event.id, readerId));
      });

      this.events.forEach((event, id) => {
        if (
          this.succ.has(id) ||
          this.pred.has(id) ||
          this.readsOf.has(id) ||
          this.readersOf.has(id)
        ) {
          this.activeIds.add(id);
        }
      });

      this.chainEndIds = new Set(
        [...this.activeIds].filter((id) => !(this.succ.get(id) || []).length),
      );

      this._computeClocks();
      return this;
    }

    // An event no clock root reaches is one nothing paces; the builder leaves its
    // frequency unset for the same reason.
    _computeClocks() {
      this.clocksOf.clear();
      this.clockRootIds = [...this.events.values()]
        .filter((event) => CLOCK_TYPES.has(event.type))
        .map((event) => event.id);

      this.clockRootIds.forEach((rootId) => {
        const stack = [rootId];
        const seen = new Set();
        while (stack.length) {
          const id = stack.pop();
          if (seen.has(id)) continue;
          seen.add(id);
          if (!this.clocksOf.has(id)) this.clocksOf.set(id, new Set());
          this.clocksOf.get(id).add(rootId);
          (this.succ.get(id) || []).forEach((next) => {
            if (!seen.has(next)) stack.push(next);
          });
        }
      });
    }

    // ── Queries ─────────────────────────────────────────────────────────────────

    edgeId(fromId, toId) {
      return this.edgeIdByKey.get(`${fromId}>${toId}`) ?? null;
    }

    ownerOf(eventId) {
      const event = this.events.get(eventId);
      return event ? this.instances.get(event.ownerId)?.data || null : null;
    }

    // An `and` event fires at the slowest of its triggers, so triggers arriving
    // at different rates mean the declared rate cannot hold for all of them.
    rateMismatch(event) {
      if (event.type !== "and") return null;
      const rates = new Set(
        (this.pred.get(event.id) || [])
          .map((id) => this.events.get(id)?.frequency)
          .filter((frequency) => frequency !== null && frequency !== undefined),
      );
      return rates.size > 1 ? [...rates].sort((a, b) => a - b) : null;
    }

    // The message type a port event carries; a process event carries none.
    msgType(event) {
      return event?.port?.msg_type || null;
    }

    // The queues an event reads when it runs, and the events reading a queue.
    queuesReadBy(eventId) {
      return this.readsOf.get(eventId) || [];
    }

    readersOfQueue(queueId) {
      return this.readersOf.get(queueId) || [];
    }

    // The clock roots at the head of a port's stream: the walk upstream that
    // stays on the port's message type, through links, the gates that publish
    // and the gates that trigger them, and stops at a periodic gate. Inputs of
    // another type along the way are side inputs of the stream, not its
    // sources, so the walk never enters them; that keeps it local in a cyclic
    // graph.
    streamRoots(portId) {
      const port = this.events.get(portId);
      const type = this.msgType(port);
      if (!port || !type) return new Set();
      const roots = new Set();
      const seen = new Set();
      const stack = [portId];
      while (stack.length) {
        const id = stack.pop();
        if (seen.has(id)) continue;
        seen.add(id);
        const event = this.events.get(id);
        if (!event) continue;
        if (event.kind === "process" && CLOCK_TYPES.has(event.type)) {
          roots.add(id);
          continue;
        }
        (this.pred.get(id) || []).forEach((predId) => {
          const pred = this.events.get(predId);
          if (!pred) return;
          if (pred.kind === "process" || this.msgType(pred) === type) {
            stack.push(predId);
          }
        });
      }
      return roots;
    }

    // Clock roots that drive distinct streams of one message type into one
    // gate are peers: the gate merges their chains, so they are analyzed as
    // one. Two streams pair the roots each has and the other lacks; a root
    // both streams share (a fork that rejoins) pairs with nothing there. Every
    // root is in exactly one set; a root without peers stands alone. Sets are
    // ordered by their first root's owner path.
    peerRoots() {
      const parent = new Map(this.clockRootIds.map((id) => [id, id]));
      const find = (id) => {
        while (parent.get(id) !== id) {
          parent.set(id, parent.get(parent.get(id)));
          id = parent.get(id);
        }
        return id;
      };
      const union = (a, b) => {
        const ra = find(a);
        const rb = find(b);
        if (ra !== rb) parent.set(ra, rb);
      };

      this.events.forEach((gate) => {
        if (gate.kind !== "process") return;
        const byType = new Map();
        (this.pred.get(gate.id) || []).forEach((predId) => {
          const pred = this.events.get(predId);
          const type = pred?.kind === "input" ? this.msgType(pred) : null;
          if (!type) return;
          if (!byType.has(type)) byType.set(type, []);
          byType.get(type).push(predId);
        });
        byType.forEach((ports) => {
          if (ports.length < 2) return;
          const streams = ports
            .map((portId) => this.streamRoots(portId))
            .filter((roots) => roots.size);
          for (let i = 0; i < streams.length; i += 1) {
            for (let j = i + 1; j < streams.length; j += 1) {
              const own = [...streams[i]].filter((id) => !streams[j].has(id));
              const other = [...streams[j]].filter((id) => !streams[i].has(id));
              if (!own.length || !other.length) continue;
              [...own, ...other].forEach((id) => union(own[0], id));
            }
          }
        });
      });

      const sets = new Map();
      this.clockRootIds.forEach((id) => {
        const root = find(id);
        if (!sets.has(root)) sets.set(root, []);
        sets.get(root).push(id);
      });
      const pathOf = (id) => this.ownerOf(id)?.path || "";
      return [...sets.values()]
        .map((ids) => ids.sort((a, b) => pathOf(a).localeCompare(pathOf(b))))
        .sort((a, b) => pathOf(a[0]).localeCompare(pathOf(b[0])));
    }

    // Walks the trigger relation in one direction and returns the events reached,
    // in hop order, together with the edges the walk used.
    walk(startIds, direction = "down") {
      const adjacency = direction === "up" ? this.pred : this.succ;
      const order = [];
      const hops = new Map(startIds.map((id) => [id, 0]));
      const edges = new Set();
      const queue = [...startIds];
      const seen = new Set(startIds);

      for (let cursor = 0; cursor < queue.length; cursor += 1) {
        const id = queue[cursor];
        (adjacency.get(id) || []).forEach((nextId) => {
          const edgeId =
            direction === "up"
              ? this.edgeId(nextId, id)
              : this.edgeId(id, nextId);
          if (edgeId) edges.add(edgeId);
          if (seen.has(nextId)) return;
          seen.add(nextId);
          hops.set(nextId, (hops.get(id) || 0) + 1);
          order.push(nextId);
          queue.push(nextId);
        });
      }
      return { order, hops, edges };
    }

    // The events reachable from a start in one direction, the start included.
    reach(startId, direction = "down") {
      const { order } = this.walk([startId], direction);
      return new Set([startId, ...order]);
    }
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { EventGraph };
  } else {
    window.EventGraph = EventGraph;
  }
})();
