// Unit tests for the build job log part: the job state label and the log's
// sequence tracking across event batches and resynchronisation.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const PART = path.join(
  __dirname,
  "..",
  "..",
  "autoware_system_designer_studio",
  "web",
  "js",
  "activities",
  "build",
  "job_log.js",
);

function fakeElement(tag) {
  const node = {
    tag,
    children: [],
    textContent: "",
    hidden: false,
    append(...nodes) {
      node.children.push(...nodes);
    },
    appendChild(child) {
      node.children.push(child);
      return child;
    },
    replaceChildren(...nodes) {
      node.children = nodes;
    },
  };
  return node;
}

const handlers = {};
const terminals = [];
global.window = {
  SD: {
    activities: {},
    on: (name, handler) => {
      handlers[name] = handler;
    },
    widgets: {
      element: (tag, _className, text) => {
        const node = fakeElement(tag);
        if (text !== undefined) node.textContent = text;
        return node;
      },
      terminal: () => {
        const lines = [];
        const t = {
          element: fakeElement("pre"),
          lines,
          append: (l) => lines.push(...l),
        };
        terminals.push(t);
        return t;
      },
      mainView: () => {
        const view = {
          element: fakeElement("div"),
          head: fakeElement("div"),
          trailing: fakeElement("div"),
          body: fakeElement("div"),
          title: "",
          empty: "",
          setTitle(text) {
            view.title = text;
          },
          setEmpty(text) {
            view.empty = text;
          },
        };
        return view;
      },
    },
  },
};
require(PART);
const build = global.window.SD.activities.build;

test("job states label with cancellation and exit code", () => {
  assert.equal(
    build.stateLabel({ state: "running", cancel_requested: false }),
    "running",
  );
  assert.equal(
    build.stateLabel({ state: "running", cancel_requested: true }),
    "cancelling",
  );
  assert.equal(
    build.stateLabel({ state: "failed", exit_code: 2 }),
    "failed (2)",
  );
  assert.equal(
    build.stateLabel({ state: "failed", exit_code: null }),
    "failed",
  );
  assert.equal(
    build.stateLabel({ state: "succeeded", exit_code: 0 }),
    "succeeded",
  );
});

test("the log follows event batches and refetches across a gap", async () => {
  const calls = [];
  const daemon = {
    rpc: async (method, params) => {
      calls.push([method, params]);
      return {
        job: {
          id: params.job,
          state: "running",
          argv: ["colcon", "build"],
          detail: {},
        },
        log: {
          first_seq: params.since + 1,
          lines: [`line ${params.since + 1}`],
        },
      };
    },
  };
  const errors = [];
  const log = build.jobLog(fakeElement("main"), daemon, (text) =>
    errors.push(text),
  );

  log.select("job-1");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, [["jobs.get", { job: "job-1", since: 0 }]]);
  assert.deepEqual(terminals[0].lines, ["line 1"]);

  // Overlapping batch: only lines past the last shown are appended.
  handlers["daemon.job.log"]({
    job: "job-1",
    first_seq: 1,
    lines: ["line 1", "line 2"],
  });
  assert.deepEqual(terminals[0].lines, ["line 1", "line 2"]);

  // Another job's batch is ignored; a gap refetches from the last line shown.
  handlers["daemon.job.log"]({ job: "job-2", first_seq: 3, lines: ["x"] });
  handlers["daemon.job.log"]({ job: "job-1", first_seq: 5, lines: ["line 5"] });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls[1], ["jobs.get", { job: "job-1", since: 2 }]);
  assert.deepEqual(terminals[0].lines, ["line 1", "line 2", "line 3"]);
  assert.deepEqual(errors, []);
});
