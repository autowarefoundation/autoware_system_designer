// Unit tests for the workbench selection store, on a stub window.
// Run with: node --test test/js/*.test.js

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const CORE_DIR = path.join(
  __dirname,
  "..",
  "..",
  "autoware_system_designer_studio",
  "web",
  "js",
  "core",
);

// Loads sd.js and store.js against a page at `search`; returns SD and the URLs written.
function loadStore(search) {
  const written = [];
  global.window = {
    location: { search, href: `http://studio.test/systems/pkg/sys/index.html${search}` },
  };
  global.history = { replaceState: (_state, _title, url) => written.push(url) };
  delete require.cache[path.join(CORE_DIR, "sd.js")];
  delete require.cache[path.join(CORE_DIR, "store.js")];
  require(path.join(CORE_DIR, "sd.js"));
  require(path.join(CORE_DIR, "store.js"));
  return { SD: global.window.SD, written };
}

test("define takes the URL value when accepted and the fallback otherwise", () => {
  const { SD } = loadStore("?mode=Planning&diagram=bogus");
  SD.store.define("mode", "Runtime", (v) => ["Runtime", "Planning"].includes(v));
  SD.store.define("diagram", "node_diagram", (v) => v === "node_diagram");
  SD.store.define("unit", "");
  assert.equal(SD.store.get("mode"), "Planning");
  assert.equal(SD.store.get("diagram"), "node_diagram");
  assert.equal(SD.store.get("unit"), "");
});

test("set writes every defined key to the URL and emits the change once", () => {
  const { SD, written } = loadStore("");
  SD.store.define("mode", "Runtime");
  SD.store.define("job", "");
  const changes = [];
  SD.store.onChange("job", (value, previous) => changes.push([value, previous]));
  SD.store.set("job", "j1");
  SD.store.set("job", "j1");
  SD.store.set("job", "");
  assert.deepEqual(changes, [
    ["j1", ""],
    ["", "j1"],
  ]);
  assert.deepEqual(
    written.map((url) => new URL(url).search),
    ["?mode=Runtime&job=j1", "?mode=Runtime"],
  );
});

test("syncUrl drops URL keys that were not defined and keeps the rest", () => {
  const { SD, written } = loadStore("?activity=build&mode=Runtime");
  SD.store.define("mode", "Runtime");
  SD.store.syncUrl();
  assert.equal(new URL(written.at(-1)).search, "?activity=build&mode=Runtime");
});
