// Launch: CPU, memory and GPU use of the launched members of a mode, filtered
// over a window. The runtimes read it only while followed: the feed follows
// every connected session of the mode while some view wants it, and releases
// them once none does. Per member the window gives the mean CPU in cores, the
// mean and peak RSS, the latest PSS and, where the GPU reports processes, the
// mean GPU memory and utilization; per unit the same for the host and its GPU
// devices. Readings of an earlier launch of a unit are dropped once it is
// launched again. The container split asks the tracer for callback CPU time
// and follows the topic-rate monitor for it, under a caller name of its own;
// it is off unless switched on, and goes off once no view wants the feed.
// Page event: "launch.resources" after every reading and split change.
(function () {
  const SD = window.SD;
  const launch = (SD.activities.launch ||= {});

  const WINDOWS = [5, 10, 30, 60];
  const DEFAULT_WINDOW = 10;
  const WINDOW_KEY = "launch.resources.window";
  // Readings kept per unit, past the longest window.
  const HISTORY_MS = (WINDOWS[WINDOWS.length - 1] + 5) * 1000;
  // Level thresholds of the icons: CPU in cores, memory and GPU memory as a
  // share of the device's.
  const LEVELS = {
    cpu: [0.25, 0.5, 1],
    mem: [0.02, 0.05, 0.1],
    gpu: [0.02, 0.05, 0.1],
  };

  function storedWindow() {
    const value = Number(SD.storage.get(WINDOW_KEY));
    return WINDOWS.includes(value) ? value : DEFAULT_WINDOW;
  }

  // Readings of one unit within `windowS` seconds of its latest, oldest first.
  function within(readings, windowS) {
    if (!readings.length) return [];
    const last = readings[readings.length - 1].time_ns;
    const from = last - windowS * 1e9;
    return readings.filter((reading) => reading.time_ns > from);
  }

  // Mean of `values` weighted by `weights`; null without any.
  function weightedMean(values, weights) {
    let sum = 0;
    let total = 0;
    values.forEach((value, i) => {
      if (value === undefined || value === null) return;
      sum += value * weights[i];
      total += weights[i];
    });
    return total > 0 ? sum / total : null;
  }

  // Use of the member at `path` over `readings`: null when none holds it.
  function memberUsage(readings, path) {
    const held = readings.filter((reading) => reading.members?.[path]);
    if (!held.length) return null;
    const entries = held.map((reading) => reading.members[path]);
    const spans = held.map((reading) => reading.interval_s || 0);
    const ones = held.map(() => 1);
    const latest = entries[entries.length - 1];
    const pss = entries
      .map((entry) => entry.pss_kb)
      .filter((value) => value !== undefined);
    return {
      procs: latest.procs,
      threads: latest.threads,
      cpu: weightedMean(
        entries.map((entry) => entry.cpu),
        spans,
      ),
      rss_kb: weightedMean(
        entries.map((entry) => entry.rss_kb),
        ones,
      ),
      rss_peak_kb: Math.max(...entries.map((entry) => entry.rss_kb || 0)),
      pss_kb: pss.length ? pss[pss.length - 1] : null,
      gpu_mem_kb: weightedMean(
        entries.map((entry) => entry.gpu_mem_kb),
        ones,
      ),
      gpu_util: weightedMean(
        entries.map((entry) => entry.gpu_util),
        ones,
      ),
      span_s: (held[held.length - 1].time_ns - held[0].time_ns) / 1e9,
    };
  }

  // Host and GPU use of one unit over `readings`: null without any.
  function hostUsage(readings) {
    if (!readings.length) return null;
    const latest = readings[readings.length - 1];
    const spans = readings.map((reading) => reading.interval_s || 0);
    const ones = readings.map(() => 1);
    const devices = (latest.gpu?.devices || []).map((device) => ({
      ...device,
      util: weightedMean(
        readings.map(
          (reading) =>
            (reading.gpu?.devices || []).find((d) => d.index === device.index)
              ?.util,
        ),
        ones,
      ),
    }));
    return {
      cpus: latest.host?.cpus || 1,
      cpu: weightedMean(
        readings.map((reading) => reading.host?.cpu),
        spans,
      ),
      mem_total_kb: latest.host?.mem_total_kb || 0,
      mem_used_kb: weightedMean(
        readings.map((reading) => reading.host?.mem_used_kb),
        ones,
      ),
      gpu: {
        source: latest.gpu?.source || null,
        per_process: !!latest.gpu?.per_process,
        note: latest.gpu?.note || "",
        devices,
      },
    };
  }

  function levelOf(value, steps) {
    if (!(value > 0)) return 0;
    let level = 0;
    steps.forEach((step) => {
      if (value >= step) level += 1;
    });
    return level;
  }

  // Icon levels 0..3 of a member's use on a unit with `host` use.
  function levels(usage, host) {
    if (!usage) return { cpu: 0, mem: 0, gpu: 0 };
    const memTotal = host?.mem_total_kb || 0;
    const gpuTotal = (host?.gpu?.devices || []).reduce(
      (sum, device) => sum + (device.mem_total_kb || 0),
      0,
    );
    return {
      cpu: levelOf(usage.cpu, LEVELS.cpu),
      mem: memTotal ? levelOf(usage.rss_kb / memTotal, LEVELS.mem) : 0,
      gpu: gpuTotal ? levelOf(usage.gpu_mem_kb / gpuTotal, LEVELS.gpu) : 0,
    };
  }

  function bytesLabel(kb) {
    if (kb === null || kb === undefined) return "–";
    if (kb >= 1048576) return `${(kb / 1048576).toFixed(1)} GB`;
    if (kb >= 1024) return `${(kb / 1024).toFixed(kb >= 102400 ? 0 : 1)} MB`;
    return `${Math.round(kb)} KB`;
  }

  function cpuLabel(cores) {
    if (cores === null || cores === undefined) return "–";
    return `${Math.round(cores * 100)} %`;
  }

  // Lines describing a member's use, for tooltips.
  function usageLines(usage) {
    if (!usage) return [];
    const lines = [
      `CPU ${cpuLabel(usage.cpu)} of a core`,
      `memory ${bytesLabel(usage.rss_kb)} RSS (peak ${bytesLabel(usage.rss_peak_kb)})` +
        (usage.pss_kb !== null ? ` · ${bytesLabel(usage.pss_kb)} PSS` : ""),
    ];
    if (usage.gpu_mem_kb !== null || usage.gpu_util !== null)
      lines.push(
        `GPU ${bytesLabel(usage.gpu_mem_kb)}` +
          (usage.gpu_util !== null ? ` · ${Math.round(usage.gpu_util)} %` : ""),
      );
    lines.push(
      `${usage.procs} process${usage.procs === 1 ? "" : "es"} · ${usage.threads} threads · over ${Math.round(usage.span_s)} s`,
    );
    return lines;
  }

  // "CPU 3.2/20 · RAM 12.1/31.0 GB · GPU 18 %": a unit's totals.
  function hostLabel(host) {
    if (!host) return "";
    const parts = [];
    if (host.cpu !== null)
      parts.push(`CPU ${host.cpu.toFixed(1)}/${host.cpus}`);
    if (host.mem_total_kb)
      parts.push(
        `RAM ${(host.mem_used_kb / 1048576).toFixed(1)}/${(host.mem_total_kb / 1048576).toFixed(1)} GB`,
      );
    host.gpu.devices.forEach((device) => {
      if (device.util !== null && device.util !== undefined)
        parts.push(
          `GPU${host.gpu.devices.length > 1 ? device.index : ""} ${Math.round(device.util)} %`,
        );
    });
    return parts.join(" · ");
  }

  function resourceFeed(config, store) {
    const daemon = SD.daemon;
    const ofMode = launch.scope(config.systemId, () => store.get("mode"));
    // Views wanting the feed.
    const wanters = new Set();
    // Units followed, and per unit the latest follow or unfollow request; the
    // next one leaves once it is answered, so the daemon sees them in order.
    const followed = new Set();
    const requests = new Map();
    // Unit -> { job, readings }.
    const units = new Map();
    let windowS = storedWindow();
    let syncQueued = false;
    // Container split: wanted, the units it holds, and unit -> { job, snapshots } of the monitor.
    const SPLIT_CLIENT = `${daemon?.clientId}/resources`;
    let split = false;
    const splitting = new Set();
    const monitors = new Map();

    function rpc(unit, method) {
      const request = (requests.get(unit) || Promise.resolve()).then(() =>
        daemon.rpc(method, { compute_unit: unit, client: daemon.clientId }),
      );
      requests.set(
        unit,
        request.catch(() => {}),
      );
      return request;
    }

    function splitRpc(unit, method, params = {}) {
      const key = `split:${unit}`;
      const request = (requests.get(key) || Promise.resolve()).then(() =>
        daemon.rpc(method, {
          ...params,
          compute_unit: unit,
          client: SPLIT_CLIENT,
        }),
      );
      requests.set(
        key,
        request.catch(() => {}),
      );
      return request;
    }

    // Holds the split on the followed units that trace, while it is wanted.
    function syncSplit() {
      if (!wanters.size && split) {
        split = false;
        SD.emit("launch.resources");
      }
      const wanted = new Set();
      if (split && daemon?.offers("monitor.options"))
        followed.forEach((unit) => {
          const session = ofMode.sessions.get(unit);
          if (session?.control?.monitor && session.control.tracer_options)
            wanted.add(unit);
        });
      wanted.forEach((unit) => {
        if (splitting.has(unit)) return;
        splitting.add(unit);
        splitRpc(unit, "monitor.follow")
          .then(() => splitRpc(unit, "monitor.options", { cpu: true }))
          .catch((error) => {
            splitting.delete(unit);
            console.warn("container split:", error);
          });
      });
      splitting.forEach((unit) => {
        if (wanted.has(unit)) return;
        splitting.delete(unit);
        monitors.delete(unit);
        splitRpc(unit, "monitor.options", { cpu: false }).catch(() => {});
        splitRpc(unit, "monitor.unfollow").catch(() => {});
      });
    }

    function sync() {
      const wanted = new Set();
      if (wanters.size && daemon?.offers("resources.follow"))
        ofMode.sessions.forEach((session, unit) => {
          if (
            session.state !== "exited" &&
            session.connected &&
            session.control?.resources
          )
            wanted.add(unit);
        });
      wanted.forEach((unit) => {
        if (followed.has(unit)) return;
        followed.add(unit);
        rpc(unit, "resources.follow").catch((error) => {
          followed.delete(unit);
          console.warn("resources:", error);
        });
      });
      followed.forEach((unit) => {
        if (wanted.has(unit)) return;
        followed.delete(unit);
        units.delete(unit);
        rpc(unit, "resources.unfollow").catch(() => {});
      });
      syncSplit();
    }

    function accept(event) {
      const unit = event.compute_unit;
      if (!followed.has(unit)) return;
      const job = ofMode.sessions.get(unit)?.job;
      let held = units.get(unit);
      if (!held || held.job !== job) {
        held = { job, readings: [] };
        units.set(unit, held);
      }
      held.readings.push(event);
      const from = event.time_ns - HISTORY_MS * 1e6;
      while (held.readings.length && held.readings[0].time_ns < from)
        held.readings.shift();
      SD.emit("launch.resources");
    }

    SD.on("daemon.monitor", (event) => {
      const unit = event.compute_unit;
      if (
        event.system !== config.systemId ||
        event.mode !== store.get("mode") ||
        !splitting.has(unit)
      )
        return;
      const job = ofMode.sessions.get(unit)?.job;
      let held = monitors.get(unit);
      if (!held || held.job !== job) {
        held = { job, snapshots: [] };
        monitors.set(unit, held);
      }
      const cpu = new Map();
      (event.nodes || []).forEach((node) => {
        if (node.cpu !== undefined) cpu.set(node.node, node.cpu);
      });
      held.snapshots.push({ time_ns: event.time_ns, cpu });
      const from = event.time_ns - HISTORY_MS * 1e6;
      while (held.snapshots.length && held.snapshots[0].time_ns < from)
        held.snapshots.shift();
      SD.emit("launch.resources");
    });
    SD.on("daemon.resources", (event) => {
      if (event.system !== config.systemId || event.mode !== store.get("mode"))
        return;
      accept(event);
    });
    SD.on("launch.sessions", sync);
    // The daemon drops every follow with the last event stream; a reconnected page follows anew.
    SD.on("daemon.connection", ({ connected }) => {
      if (!connected) return;
      followed.clear();
      splitting.clear();
      sync();
    });
    store.onChange("mode", () => {
      followed.forEach((unit) =>
        rpc(unit, "resources.unfollow").catch(() => {}),
      );
      followed.clear();
      units.clear();
      sync();
      SD.emit("launch.resources");
    });

    // CPU of the callbacks of the node at `path` over the window, in cores; null
    // without the split or a reading of it.
    function nodeCpu(path) {
      for (const held of monitors.values()) {
        const values = within(held.snapshots, windowS)
          .map((snapshot) => snapshot.cpu.get(path))
          .filter((value) => value !== undefined);
        if (values.length)
          return values.reduce((a, b) => a + b, 0) / values.length;
      }
      return null;
    }

    // The windowed readings of `unit`.
    const windowed = (unit) => within(units.get(unit)?.readings || [], windowS);

    return {
      WINDOWS,
      // Starts or ends the want of view `id`; the feed runs while any view
      // wants it. One view handing over to another within a task keeps it.
      want(id, on) {
        if (on) wanters.add(id);
        else wanters.delete(id);
        if (syncQueued) return;
        syncQueued = true;
        queueMicrotask(() => {
          syncQueued = false;
          sync();
        });
      },
      get window() {
        return windowS;
      },
      setWindow(seconds) {
        if (!WINDOWS.includes(seconds)) return;
        windowS = seconds;
        SD.storage.set(WINDOW_KEY, String(seconds));
        SD.emit("launch.resources");
      },
      // Whether the container split is wanted, and whether any unit splits now.
      get split() {
        return split;
      },
      get splitting() {
        return monitors.size > 0;
      },
      setSplit(on) {
        split = !!on;
        syncSplit();
        SD.emit("launch.resources");
      },
      nodeCpu,
      // Units with readings.
      units() {
        return [...units.keys()];
      },
      // Use of the member at `path` over the window, with its unit; null when no unit reads it.
      usage(path) {
        for (const unit of units.keys()) {
          const usage = memberUsage(windowed(unit), path);
          if (usage) return { ...usage, unit };
        }
        return null;
      },
      // Host and GPU use of `unit` over the window; null without readings.
      host(unit) {
        return hostUsage(windowed(unit));
      },
    };
  }

  let shared = null;
  launch.resourceFeed = (config, store) => {
    shared ||= resourceFeed(config, store);
    return shared;
  };
  launch.resourceUsage = {
    within,
    memberUsage,
    hostUsage,
    levels,
    usageLines,
    hostLabel,
    bytesLabel,
    cpuLabel,
  };
})();
