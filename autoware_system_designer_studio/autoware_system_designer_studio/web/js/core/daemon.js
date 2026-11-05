// Client of the studio daemon that served this page, when one did.
// A page opened from file:// or from a plain file server has no daemon; its
// health probe fails and the workbench stays a static viewer.
//
// Page events: "daemon.connection" {connected}, "daemon.health" (health result),
// "daemon.jobs" (job list), and "daemon.<topic>" for every event-stream topic
// (job, job.log, systems.changed, daemon, member, member.log).
(function () {
  const SD = window.SD;
  const CLIENT_HEADER = "X-System-Designer-Client";
  const STREAM_TOPICS = ["job", "job.log", "systems.changed", "daemon", "member", "member.log"];

  let root = "/";
  let health = null;
  let connected = false;
  let rpcId = 0;

  function apiUrl(path) {
    return `${root}api/${path}`;
  }

  async function rpc(method, params = {}) {
    rpcId += 1;
    const response = await fetch(apiUrl("rpc"), {
      method: "POST",
      headers: { "Content-Type": "application/json", [CLIENT_HEADER]: "workbench" },
      body: JSON.stringify({ id: rpcId, method, params }),
    });
    const body = await response.json();
    if (!body.ok) throw new Error(`${body.error.code}: ${body.error.message}`);
    return body.result;
  }

  async function refreshHealth() {
    health = await rpc("health");
    SD.emit("daemon.health", health);
    return health;
  }

  // Jobs known to this page, newest first; "daemon.jobs" carries the full list on every change.
  const jobs = new Map();

  function emitJobs() {
    SD.emit("daemon.jobs", jobList());
  }

  function jobList() {
    return Array.from(jobs.values()).sort((a, b) => b.created_at - a.created_at);
  }

  function upsertJob(job) {
    jobs.set(job.id, job);
    emitJobs();
  }

  async function refreshJobs() {
    const result = await rpc("jobs.list");
    jobs.clear();
    result.jobs.forEach((job) => jobs.set(job.id, job));
    emitJobs();
  }

  function setConnected(value) {
    if (connected === value) return;
    connected = value;
    SD.emit("daemon.connection", { connected });
  }

  function openStream() {
    const source = new EventSource(apiUrl("events"));
    // The browser reconnects on its own; each hello resynchronises through the request API.
    source.addEventListener("error", () => setConnected(false));
    source.addEventListener("hello", () => {
      setConnected(true);
      refreshHealth().catch((error) => console.warn("daemon health:", error));
      refreshJobs().catch((error) => console.warn("daemon jobs:", error));
    });
    STREAM_TOPICS.forEach((topic) => {
      source.addEventListener(topic, (event) => {
        const data = JSON.parse(event.data);
        if (topic === "job") upsertJob(data);
        if (topic === "daemon") refreshHealth().catch(() => {});
        SD.emit(`daemon.${topic}`, data);
      });
    });
  }

  // Resolves to the health result, or null when no daemon answers under `siteRoot`.
  async function probe(siteRoot) {
    root = siteRoot || "/";
    if (!/^https?:$/.test(window.location.protocol)) return null;
    try {
      const response = await fetch(apiUrl("health"), { cache: "no-store" });
      if (!response.ok) return null;
      const body = await response.json();
      if (!body.ok) return null;
      health = body.result;
    } catch (error) {
      return null;
    }
    openStream();
    return health;
  }

  function offers(method) {
    return !!health && (health.methods || []).includes(method);
  }

  SD.daemon = {
    probe,
    rpc,
    refreshHealth,
    offers,
    apiUrl,
    upsertJob,
    jobs: jobList,
    // Address of a daemon-only page, e.g. "daemon/build.html".
    pageUrl: (path) => `${root}${path}`,
    get health() {
      return health;
    },
    get connected() {
      return connected;
    },
  };
})();
