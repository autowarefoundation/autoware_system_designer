// Unit tests for the Control view's member bar: which selections carry
// actions, the states each action applies to, the confirmation of Stop and
// Kill, and the exact target of every request, on a stub DOM and daemon.

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
  "launch",
  "member_actions.js",
);

function fakeElement(tag, className = "", text = "") {
  const node = {
    tag,
    className,
    textContent: text,
    children: [],
    hidden: false,
    disabled: false,
    title: "",
    listeners: {},
    append(...items) {
      node.children.push(...items);
    },
    appendChild(item) {
      node.children.push(item);
    },
    replaceChildren(...items) {
      node.children = [...items];
    },
    addEventListener(type, handler) {
      node.listeners[type] = handler;
    },
    click() {
      node.listeners.click?.();
    },
  };
  return node;
}

function walk(node, visit) {
  visit(node);
  (node.children || []).forEach((child) => walk(child, visit));
}

function buttons(bar) {
  const found = [];
  walk(bar, (node) => {
    if (node.tag === "button") found.push(node);
  });
  return found;
}

function button(bar, label) {
  return buttons(bar).find((b) => b.textContent === label);
}

function texts(bar) {
  const found = [];
  walk(bar, (node) => {
    if (node.tag !== "button" && node.textContent) found.push(node.textContent);
  });
  return found;
}

const calls = [];
let answer = () => Promise.resolve({ members: [] });
const handlers = {};
const members = new Map();
const sessions = new Map();

global.window = {
  SD: {
    activities: {
      launch: {
        scope: () => ({
          members,
          sessions,
          restartsOf: () => ({ restarts: 0, respawns: 0 }),
        }),
        memberState: (entity, records) => {
          const record = records.get(entity.member_path)?.record;
          return { token: "running", label: record?.state || "idle" };
        },
      },
    },
    widgets: { element: fakeElement },
    RunState: { mark: () => fakeElement("span", "state-dot") },
    on: (name, handler) => {
      (handlers[name] ||= []).push(handler);
    },
    daemon: {
      offers: () => true,
      rpc: (method, params) => {
        calls.push([method, params]);
        return answer(method, params);
      },
    },
  },
};
require(PART);
const { memberActions } = global.window.SD.activities.launch;

function makeStore(entity) {
  const values = { entity, mode: "default" };
  const listeners = {};
  return {
    get: (key) => values[key],
    set(key, value) {
      values[key] = value;
      (listeners[key] || []).forEach((handler) => handler(value));
    },
    onChange(key, handler) {
      (listeners[key] ||= []).push(handler);
    },
  };
}

const ENTITIES = [
  { key: "n:/a/x", entity_type: "node", path: "n:/a/x", member_path: "/a/x" },
  {
    key: "n:/c",
    entity_type: "node",
    path: "n:/c",
    member_path: "/c",
  },
  {
    key: "n:/a/comp",
    entity_type: "node",
    path: "n:/c/comp",
    member_path: "/a/comp",
    container_path: "/c",
  },
  { key: "m:/a", entity_type: "module", path: "/a" },
];
const hooks = {
  entityOf: (key) => ENTITIES.find((e) => e.key === key) || null,
  containerOf: (entity) =>
    ENTITIES.find(
      (e) => !e.container_path && e.member_path === entity.container_path,
    ) || null,
};

function setLive(state, connected = true) {
  members.clear();
  sessions.clear();
  members.set("/a/x", { unit: "main_ecu", record: { state } });
  members.set("/c", { unit: "main_ecu", record: { state: "running" } });
  sessions.set("main_ecu", {
    compute_unit: "main_ecu",
    job: "j1",
    state: "running",
    connected,
    control: { policy: "guard" },
  });
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

test("a running node offers its actions by state", () => {
  setLive("running");
  const bar = memberActions(
    { systemId: "pkg:Sys" },
    makeStore("n:/a/x"),
    hooks,
  );
  assert.deepEqual(
    buttons(bar.element).map((b) => [b.textContent, b.disabled]),
    [
      ["Stop", false],
      ["Restart", false],
      ["Kill", false],
    ],
  );
  setLive("stopped");
  bar.render();
  assert.deepEqual(
    buttons(bar.element).map((b) => [b.textContent, b.disabled]),
    [
      ["Stop", true],
      ["Restart", false],
      ["Kill", true],
    ],
  );
  setLive("running", false);
  bar.render();
  assert.ok(buttons(bar.element).every((b) => b.disabled));
});

test("restart is sent at once with the exact member path", async () => {
  setLive("running");
  calls.length = 0;
  const bar = memberActions(
    { systemId: "pkg:Sys" },
    makeStore("n:/a/x"),
    hooks,
  );
  button(bar.element, "Restart").click();
  assert.deepEqual(calls, [
    [
      "launch.restart",
      { compute_unit: "main_ecu", pattern: "/a/x", exact: true },
    ],
  ]);
  assert.ok(buttons(bar.element).every((b) => b.disabled));
  await flush();
  assert.equal(button(bar.element, "Restart").disabled, false);
});

test("stop and kill are confirmed first, and a selection change drops the confirmation", () => {
  setLive("running");
  calls.length = 0;
  const store = makeStore("n:/a/x");
  const bar = memberActions({ systemId: "pkg:Sys" }, store, hooks);
  button(bar.element, "Stop").click();
  assert.equal(calls.length, 0);
  assert.ok(texts(bar.element).includes("Stop this process?"));
  button(bar.element, "Cancel").click();
  assert.equal(calls.length, 0);
  button(bar.element, "Kill").click();
  store.set("entity", "n:/c");
  assert.ok(!texts(bar.element).includes("Kill this process?"));
  button(bar.element, "Kill").click();
  button(bar.element, "Kill").click();
  assert.deepEqual(calls, [
    ["launch.kill", { compute_unit: "main_ecu", pattern: "/c", exact: true }],
  ]);
});

test("a refusal shows in the bar", async () => {
  setLive("running");
  answer = () => Promise.reject(new Error("restart refused: autonomous"));
  const bar = memberActions(
    { systemId: "pkg:Sys" },
    makeStore("n:/a/x"),
    hooks,
  );
  button(bar.element, "Restart").click();
  await flush();
  assert.ok(texts(bar.element).includes("restart refused: autonomous"));
  answer = () => Promise.resolve({ members: [] });
});

test("a composable node links to its container and has no actions", () => {
  setLive("running");
  const store = makeStore("n:/a/comp");
  const bar = memberActions({ systemId: "pkg:Sys" }, store, hooks);
  assert.deepEqual(
    buttons(bar.element).map((b) => b.textContent),
    ["/c"],
  );
  button(bar.element, "/c").click();
  assert.equal(store.get("entity"), "n:/c");
  assert.deepEqual(
    buttons(bar.element).map((b) => b.textContent),
    ["Stop", "Restart", "Kill"],
  );
});

test("a module and an empty selection have no actions", () => {
  setLive("running");
  const store = makeStore("m:/a");
  const bar = memberActions({ systemId: "pkg:Sys" }, store, hooks);
  assert.equal(buttons(bar.element).length, 0);
  store.set("entity", "");
  assert.equal(buttons(bar.element).length, 0);
});
