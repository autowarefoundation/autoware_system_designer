// Unit tests for the replay bag file reader: MCAP summary, file choice and dropped paths.
// Run with: node --test test/js/*.test.js

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

global.window = {
  SD: {
    replayRuntime: {
      bagFiles: {
        metadata: "metadata.yaml",
        storage: [".db3", ".mcap"],
        compressed: ".zstd",
      },
    },
  },
};
require(
  path.join(
    __dirname,
    "..",
    "..",
    "autoware_system_designer_studio",
    "web",
    "js",
    "activities",
    "replay",
    "bag_file.js",
  ),
);
const bagFile = global.window.SD.activities.replay.bagFile;

const MAGIC = [0x89, 0x4d, 0x43, 0x41, 0x50, 0x30, 0x0d, 0x0a];

function u16(value) {
  const bytes = Buffer.alloc(2);
  bytes.writeUInt16LE(value);
  return bytes;
}

function u32(value) {
  const bytes = Buffer.alloc(4);
  bytes.writeUInt32LE(value);
  return bytes;
}

function u64(value) {
  const bytes = Buffer.alloc(8);
  bytes.writeBigUInt64LE(BigInt(value));
  return bytes;
}

function str(text) {
  return Buffer.concat([u32(Buffer.byteLength(text)), Buffer.from(text)]);
}

function record(op, ...parts) {
  const body = Buffer.concat(parts);
  return Buffer.concat([Buffer.from([op]), u64(body.length), body]);
}

function schema(id, name) {
  return record(0x03, u16(id), str(name), str("ros2msg"), u32(0));
}

function channel(id, schemaId, topic) {
  return record(0x04, u16(id), u16(schemaId), str(topic), str("cdr"), u32(0));
}

function statistics(counts) {
  const pairs = Buffer.concat(
    Object.entries(counts).map(([id, count]) =>
      Buffer.concat([u16(Number(id)), u64(count)]),
    ),
  );
  return record(
    0x0b,
    u64(0),
    u16(0),
    u32(0),
    u32(0),
    u32(0),
    u32(0),
    u64(0),
    u64(0),
    u32(pairs.length),
    pairs,
  );
}

// An MCAP file of a data section of `dataSize` bytes and the summary records.
function mcap(summary, dataSize = 16, name = "bag_0.mcap") {
  const head = Buffer.concat([Buffer.from(MAGIC), Buffer.alloc(dataSize)]);
  const body = Buffer.concat(summary);
  const footer = record(
    0x02,
    u64(body.length ? head.length : 0),
    u64(0),
    u32(0),
  );
  const file = new Blob([head, body, footer, Buffer.from(MAGIC)]);
  file.name = name;
  return file;
}

test("mcapTopics reads topics, types and counts from the summary section", async () => {
  const topics = await bagFile.mcapTopics(
    mcap([
      schema(1, "std_msgs/msg/String"),
      schema(2, "tf2_msgs/msg/TFMessage"),
      channel(1, 1, "/chatter"),
      channel(2, 2, "/tf"),
      channel(3, 2, "/tf"),
      record(0x08, Buffer.alloc(12)),
      statistics({ 1: 7, 2: 30, 3: 12 }),
    ]),
  );
  assert.deepEqual(
    [...topics],
    [
      ["/chatter", { type: "std_msgs/msg/String", count: 7 }],
      ["/tf", { type: "tf2_msgs/msg/TFMessage", count: 42 }],
    ],
  );
});

test("mcapTopics refuses a file without a footer or a summary section", async () => {
  const cut = new Blob([Buffer.from(MAGIC), Buffer.alloc(64)]);
  cut.name = "cut.mcap";
  await assert.rejects(bagFile.mcapTopics(cut), /not a finished MCAP file/);
  await assert.rejects(bagFile.mcapTopics(mcap([])), /without a summary/);
});

test("choose prefers the metadata, then the MCAP files, then an opaque file", () => {
  const files = [
    "notes.txt",
    "bag_0.db3",
    "bag_1.mcap",
    "bag_0.mcap",
    "metadata.yaml",
  ].map((name) => ({ name }));
  assert.equal(bagFile.choose(files).metadata.name, "metadata.yaml");
  assert.deepEqual(
    bagFile.choose(files.slice(0, 4)).mcaps.map((file) => file.name),
    ["bag_0.mcap", "bag_1.mcap"],
  );
  assert.equal(bagFile.choose(files.slice(0, 2)).opaque.name, "bag_0.db3");
  assert.equal(
    bagFile.choose([{ name: "bag_0.mcap.zstd" }]).opaque.name,
    "bag_0.mcap.zstd",
  );
  assert.equal(bagFile.choose(files.slice(0, 1)), null);
});

test("readTopics reads the metadata among the files", async () => {
  const metadata = { name: "metadata.yaml", text: async () => "yaml" };
  const read = await bagFile.readTopics(
    [{ name: "bag_0.mcap.zstd" }, metadata],
    (text) => `${text}!`,
  );
  assert.deepEqual(
    [read.topics, read.name, read.file, read.splits],
    ["yaml!", "metadata.yaml", metadata, 0],
  );
});

test("readTopics merges the splits of a bag", async () => {
  const first = mcap(
    [
      schema(1, "std_msgs/msg/String"),
      channel(1, 1, "/chatter"),
      statistics({ 1: 7 }),
    ],
    16,
    "bag_0.mcap",
  );
  const second = mcap(
    [
      schema(4, "std_msgs/msg/String"),
      schema(5, "tf2_msgs/msg/TFMessage"),
      channel(1, 5, "/tf"),
      channel(2, 4, "/chatter"),
      statistics({ 1: 2, 2: 5 }),
    ],
    16,
    "bag_1.mcap",
  );
  const read = await bagFile.readTopics([second, first]);
  assert.deepEqual(
    [...read.topics],
    [
      ["/chatter", { type: "std_msgs/msg/String", count: 12 }],
      ["/tf", { type: "tf2_msgs/msg/TFMessage", count: 2 }],
    ],
  );
  assert.equal(read.name, "bag_0.mcap and 1 more split");
  assert.equal(read.file, first);
  assert.equal(read.splits, 2);
});

test("readTopics leaves a storage file it cannot read to the daemon", async () => {
  for (const name of ["bag_0.db3", "bag_0.mcap.zstd", "bag_0.db3.zstd"]) {
    const read = await bagFile.readTopics([{ name }]);
    assert.equal(read.topics, null);
    assert.equal(read.file.name, name);
    assert.match(bagFile.opaqueNote(name), /not read in the page/);
  }
  assert.match(bagFile.opaqueNote("bag_0.mcap.zstd"), /compressed bag file/);
  await assert.rejects(
    bagFile.readTopics([{ name: "notes.txt" }]),
    /not a metadata/,
  );
  await assert.rejects(bagFile.readTopics([]), /no metadata/);
});

test("isAbsolute takes a rooted path, a home path or a file URI", () => {
  ["/data/bag", "~/bag", "~", "file:///data/bag"].forEach((path) =>
    assert.equal(bagFile.isAbsolute(path), true, path),
  );
  ["bag", "./bag", "data/bag", "~user/bag", ""].forEach((path) =>
    assert.equal(bagFile.isAbsolute(path), false, path),
  );
});

test("droppedPath takes a file URI or an absolute path", () => {
  assert.equal(
    bagFile.droppedPath("# comment\r\nfile:///data/my%20bag/bag_0.mcap\r\n"),
    "/data/my bag/bag_0.mcap",
  );
  assert.equal(bagFile.droppedPath("/data/bag"), "/data/bag");
  assert.equal(bagFile.droppedPath("~/bag"), "~/bag");
  assert.equal(bagFile.droppedPath("https://example.com/bag"), "");
  assert.equal(bagFile.droppedPath("some text"), "");
  assert.equal(bagFile.droppedPath(""), "");
});

test("a bag path names the directory that holds its bag directory", () => {
  assert.equal(bagFile.bagCollection("/data/bags/run"), "/data/bags");
  assert.equal(
    bagFile.bagCollection("/data/bags/run/run_0.mcap"),
    "/data/bags",
  );
  assert.equal(
    bagFile.bagCollection("/data/bags/run/metadata.yaml"),
    "/data/bags",
  );
  assert.equal(bagFile.bagCollection("/run"), "");
  assert.equal(bagFile.bagCollection(""), "");
});
