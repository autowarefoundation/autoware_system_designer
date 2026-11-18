// Replay: the topics of a bag handed to the page as files, and what a drop
// carries. The metadata file is read as text, MCAP files from their summary
// sections, the splits of a bag as one; another storage file and a file
// compressed as a whole are read by the daemon. A path or `file://` URI
// dropped as text names the bag for the daemon.
(function () {
  const SD = window.SD;
  const replay = ((SD.activities ||= {}).replay ||= {});

  const MCAP_MAGIC = [0x89, 0x4d, 0x43, 0x41, 0x50, 0x30, 0x0d, 0x0a];
  // Footer record: opcode, length, summary start, summary offset start, CRC.
  const MCAP_FOOTER_SIZE = 1 + 8 + 20;
  const MCAP_OP = {
    footer: 0x02,
    schema: 0x03,
    channel: 0x04,
    statistics: 0x0b,
  };
  // Statistics record: offset of the per-channel message counts.
  const MCAP_STATISTICS_COUNTS = 42;

  // File names of a bag, from the page configuration: its `metadata` file,
  // the suffixes of its `storage` files and the suffix a file `compressed`
  // as a whole adds.
  const names = () => ({
    metadata: "",
    storage: [],
    compressed: "",
    ...(SD.replayRuntime || {}).bagFiles,
  });
  // Storage whose summary the page reads.
  const MCAP_SUFFIX = ".mcap";
  const lower = (name) => String(name).toLowerCase();
  const isMetadata = (name) => lower(name) === names().metadata;
  const isStorage = (name) =>
    names().storage.some((suffix) => lower(name).endsWith(suffix));
  const isCompressed = (name) =>
    !!names().compressed && lower(name).endsWith(names().compressed);
  const isMcap = (name) => lower(name).endsWith(MCAP_SUFFIX);
  // Storage files the page does not read: another storage, and any file compressed as a whole.
  const isOpaque = (name) =>
    isCompressed(name)
      ? isStorage(name.slice(0, -names().compressed.length))
      : isStorage(name) && !isMcap(name);
  // A file that names a bag: its metadata or one of its storage files.
  const isBagFile = (name) => isMetadata(name) || isStorage(name);
  const suffixOf = (name) => name.slice(name.lastIndexOf("."));
  // Suffixes of the files a bag is read from, for a file chooser.
  const fileSuffixes = () =>
    [suffixOf(names().metadata), ...names().storage, names().compressed].filter(
      Boolean,
    );
  const isAbsolute = (path) => /^(\/|~(\/|$)|file:\/\/)/i.test(path);
  const plural = (count, noun) => `${count} ${noun}${count === 1 ? "" : "s"}`;

  function mcapSummary(view, name) {
    const text = new TextDecoder();
    const string = (at) => {
      const length = view.getUint32(at, true);
      const bytes = new Uint8Array(
        view.buffer,
        view.byteOffset + at + 4,
        length,
      );
      return { value: text.decode(bytes), end: at + 4 + length };
    };
    const schemas = new Map();
    const channels = [];
    const counts = new Map();
    let at = 0;
    while (at + 9 <= view.byteLength) {
      const op = view.getUint8(at);
      const length = Number(view.getBigUint64(at + 1, true));
      const start = at + 9;
      at = start + length;
      if (at > view.byteLength) throw new Error(`${name}: cut summary section`);
      if (op === MCAP_OP.schema) {
        schemas.set(view.getUint16(start, true), string(start + 2).value);
      } else if (op === MCAP_OP.channel) {
        channels.push({
          id: view.getUint16(start, true),
          schema: view.getUint16(start + 2, true),
          topic: string(start + 4).value,
        });
      } else if (op === MCAP_OP.statistics) {
        const first = start + MCAP_STATISTICS_COUNTS + 4;
        const end = first + view.getUint32(first - 4, true);
        for (let pair = first; pair + 10 <= end; pair += 10)
          counts.set(
            view.getUint16(pair, true),
            Number(view.getBigUint64(pair + 2, true)),
          );
      }
    }
    const topics = new Map();
    channels.forEach((channel) => {
      const topic = topics.get(channel.topic) || {
        type: schemas.get(channel.schema) || "",
        count: 0,
      };
      topic.count += counts.get(channel.id) || 0;
      topics.set(channel.topic, topic);
    });
    return topics;
  }

  // Topics of an MCAP file (a Blob with a name): name -> { type, count }.
  async function mcapTopics(file) {
    const tailSize = MCAP_FOOTER_SIZE + MCAP_MAGIC.length;
    const tail =
      file.size >= tailSize + MCAP_MAGIC.length
        ? new DataView(await file.slice(file.size - tailSize).arrayBuffer())
        : null;
    if (
      !tail ||
      tail.getUint8(0) !== MCAP_OP.footer ||
      !MCAP_MAGIC.every(
        (byte, i) => tail.getUint8(MCAP_FOOTER_SIZE + i) === byte,
      )
    )
      throw new Error(`${file.name}: not a finished MCAP file`);
    const summaryStart = Number(tail.getBigUint64(9, true));
    if (!summaryStart)
      throw new Error(`${file.name}: MCAP file without a summary section`);
    const summary = await file
      .slice(summaryStart, file.size - tailSize)
      .arrayBuffer();
    return mcapSummary(new DataView(summary), file.name);
  }

  // Topics of the splits of one bag: the counts summed by topic.
  async function mergedTopics(files) {
    const merged = new Map();
    for (const file of files) {
      (await mcapTopics(file)).forEach((topic, name) => {
        const known = merged.get(name);
        if (!known) merged.set(name, { ...topic });
        else {
          known.count += topic.count;
          known.type ||= topic.type;
        }
      });
    }
    return merged;
  }

  // What of `files` names a bag's topics: its `metadata`, else its `mcaps` in
  // name order, else an `opaque` storage file; null when none does.
  function choose(files) {
    const byName = [...files].sort((a, b) => a.name.localeCompare(b.name));
    const metadata = byName.find((file) => isMetadata(file.name));
    if (metadata) return { metadata };
    const mcaps = byName.filter((file) => isMcap(file.name));
    if (mcaps.length) return { mcaps };
    const opaque = byName.find((file) => isOpaque(file.name));
    return opaque ? { opaque } : null;
  }

  // The bag `files` hold as { topics, name, file, splits }: `file` is the one a
  // daemon finds the bag by, `splits` the number of storage files read.
  // `topics` is null for a storage file only the daemon reads.
  async function readTopics(files, parseMetadata) {
    const chosen = choose(files);
    if (!chosen)
      throw new Error(
        files.length === 1
          ? `${files[0].name}: not a ${names().metadata} or bag file`
          : `no ${names().metadata} or bag file among the files`,
      );
    if (chosen.metadata)
      return {
        topics: parseMetadata(await chosen.metadata.text()),
        name: chosen.metadata.name,
        file: chosen.metadata,
        splits: 0,
      };
    if (chosen.opaque)
      return {
        topics: null,
        name: chosen.opaque.name,
        file: chosen.opaque,
        splits: 0,
      };
    const { mcaps } = chosen;
    return {
      topics: await mergedTopics(mcaps),
      name:
        mcaps.length === 1
          ? mcaps[0].name
          : `${mcaps[0].name} and ${plural(mcaps.length - 1, "more split")}`,
      file: mcaps[0],
      splits: mcaps.length,
    };
  }

  // Why the page does not read the storage file `name` itself.
  function opaqueNote(name) {
    const kind = isCompressed(name)
      ? "compressed bag file"
      : `${suffixOf(name)} file`;
    return `${name}: a ${kind} is not read in the page; drop its ${names().metadata}, or connect the studio daemon and give the bag's path`;
  }

  // Path a dropped text names: a `file://` URI or an absolute path, else "".
  function droppedPath(text) {
    const line = String(text || "")
      .split(/\r?\n/)
      .map((entry) => entry.trim())
      .find((entry) => entry && !entry.startsWith("#"));
    if (!line) return "";
    if (/^file:\/\//i.test(line)) {
      try {
        return decodeURIComponent(new URL(line).pathname);
      } catch (_error) {
        return "";
      }
    }
    return /^(\/|~\/)\S*$/.test(line) ? line : "";
  }

  function entryFile(entry) {
    return new Promise((resolve) => entry.file(resolve, () => resolve(null)));
  }

  function directoryFiles(directory) {
    return new Promise((resolve) => {
      const reader = directory.createReader();
      const entries = [];
      const read = () =>
        reader.readEntries(
          (batch) => {
            if (!batch.length) {
              resolve(
                Promise.all(
                  entries.filter((entry) => entry.isFile).map(entryFile),
                ),
              );
              return;
            }
            entries.push(...batch);
            read();
          },
          () => resolve([]),
        );
      read();
    });
  }

  // What a drop carries: `files`, those of a dropped directory included, and
  // the `path` its text names. The entries are taken before the first await,
  // while the event still holds them.
  async function dropped(dataTransfer) {
    const path =
      droppedPath(dataTransfer.getData("text/uri-list")) ||
      droppedPath(dataTransfer.getData("text/plain"));
    const direct = [...(dataTransfer.files || [])];
    const entries = [...(dataTransfer.items || [])]
      .filter((item) => item.kind === "file")
      .map((item) => item.webkitGetAsEntry?.())
      .filter(Boolean);
    if (!entries.length) return { files: direct, path };
    const found = await Promise.all(
      entries.map((entry) =>
        entry.isDirectory ? directoryFiles(entry) : entryFile(entry),
      ),
    );
    return { files: found.flat().filter(Boolean), path };
  }

  // Directory that holds the bag at `path` beside others: the parent of the
  // bag directory, which a path of one of its files names by that file.
  function bagCollection(path) {
    const parts = String(path || "")
      .split("/")
      .filter(Boolean);
    if (isBagFile(parts[parts.length - 1] || "")) parts.pop();
    parts.pop();
    return parts.length ? `/${parts.join("/")}` : "";
  }

  replay.bagFile = {
    metadataName: () => names().metadata,
    isBagFile,
    fileSuffixes,
    bagCollection,
    mcapTopics,
    choose,
    readTopics,
    opaqueNote,
    isAbsolute,
    droppedPath,
    dropped,
  };
})();
