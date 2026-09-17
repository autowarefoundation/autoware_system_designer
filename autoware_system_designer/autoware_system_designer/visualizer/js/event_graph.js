// Event Graph Module
// The trigger graph of an exported system: every port and process event is a
// vertex, every trigger relation an edge. Built once from the instance tree and
// read by the diagrams that draw chains over it. Free of the DOM, so the same
// module runs under node for the solver tests.

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
      this.activeIds = new Set(); // events carrying at least one trigger relation
      this.chainEndIds = new Set(); // events the chain stops at
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
          latency: event.latency ?? null,
          triggers: (event.trigger_ids || []).map(String),
          actions: (event.action_ids || []).map(String),
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
          addEvent(event, instance, "process", null),
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

      this.events.forEach((event, id) => {
        if (this.succ.has(id) || this.pred.has(id)) this.activeIds.add(id);
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
