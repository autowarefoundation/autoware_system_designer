// Replay: the rosbag of a replay, in a bar of its path and the ways to read
// it. The bag is files dropped on the view or opened, read in the page, or a
// path dropped, typed or browsed, read by the daemon; a daemon finds the path
// of a file read here. Reading a bag writes nothing into it.
(function () {
  const SD = window.SD;
  const { element } = SD.widgets;
  const replay = (SD.activities.replay ||= {});
  const { model, bagFile } = replay;
  // Preference: directories of the bags read by path, as a JSON list.
  const BAG_DIRS_KEY = "replay.bagDirs";
  const BAG_DIRS_KEPT = 5;
  const READ = "replay.bag";
  const FIND = "replay.find";

  // Directories of the bags read by path, newest first.
  function bagDirs() {
    try {
      const dirs = JSON.parse(SD.storage.get(BAG_DIRS_KEY) || "[]");
      return Array.isArray(dirs) ? dirs.filter(Boolean).map(String) : [];
    } catch (_error) {
      return [];
    }
  }

  function rememberBagDir(path) {
    const dir = model.bagCollection(path);
    if (!dir) return;
    SD.storage.set(
      BAG_DIRS_KEY,
      JSON.stringify(
        [dir, ...bagDirs().filter((d) => d !== dir)].slice(0, BAG_DIRS_KEPT),
      ),
    );
  }

  const directoryOf = (path) => path.replace(/\/[^/]*$/, "") || "/";

  // `dropTarget` takes the drops; `changed()` follows every change of the
  // bag, its path or its state.
  function bagSource({ daemon, dropTarget, changed }) {
    // Bag topics by recorded name; null until a bag is read.
    let topics = null;
    let name = "";
    // What reading the bag last failed with, shown in the bag's place.
    let note = "";
    // Latest bag request; the answer of an earlier one is dropped.
    let latest = 0;

    const root = element("div", "replay-bag-bar");
    const pathInput = element("input", "panel-search");
    pathInput.type = "text";
    pathInput.placeholder = "Bag path, or drop a bag";
    pathInput.title =
      "Absolute path the replay reads the bag from. A bag dropped on the view gives its topics; " +
      "a dropped file gives no path.";
    pathInput.setAttribute("aria-label", "Bag path");
    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.multiple = true;
    fileInput.accept = ".yaml,.yml,.mcap,.db3,.zstd";
    fileInput.hidden = true;
    const open = element("button", "panel-button", "Open file");
    open.type = "button";
    open.title =
      "Read the topics of a metadata.yaml, or of the .mcap files of one bag";
    const read = element("button", "panel-button", "Read");
    read.type = "button";
    read.title =
      "Read the topics of the bag at this path through the daemon, from its metadata.yaml or its storage file";
    const browse = element("button", "panel-button", "Browse");
    browse.type = "button";
    browse.title =
      "Choose a bag directory or file on the daemon's host, then read it";
    const picker = SD.widgets.pathPicker({ title: "Rosbag", kind: "bag" });
    const state = element("span", "replay-bag-state");
    const row = element("div", "replay-bag-row");
    row.append(browse, read, open, fileInput);
    root.append(
      element("span", "replay-bag-label", "Rosbag"),
      pathInput,
      row,
      state,
    );

    const path = () => pathInput.value.trim();
    const reads = () => daemon.connected && daemon.offers(READ);
    const finds = () => daemon.connected && daemon.offers(FIND);

    function setBag(next, title) {
      topics = next;
      name = title;
      note = "";
      state.classList.remove("blocked");
      changed();
    }

    function failed(message) {
      topics = null;
      note = message;
      state.classList.add("blocked");
      changed();
    }

    // Reads the bag at the typed path through the daemon; with `keep`, a bag
    // the daemon does not read leaves the topics read here.
    function readPath({ keep = false } = {}) {
      const target = path();
      const request = ++latest;
      if (!bagFile.isAbsolute(target)) {
        failed(`the bag path must be absolute: ${target}`);
        return;
      }
      daemon.rpc(READ, { path: target }).then(
        (info) => {
          if (request !== latest) return;
          rememberBagDir(info.path);
          setBag(
            new Map(
              info.topics.map((t) => [
                t.name,
                { type: t.type, count: t.count },
              ]),
            ),
            info.path,
          );
        },
        (error) => {
          if (request !== latest || keep) return;
          failed(
            error.message.includes(target)
              ? error.message
              : `${target}: ${error.message}`,
          );
        },
      );
    }

    // The daemon finds the path of the bag `found` was read from, under the
    // typed path, the remembered directories and its own.
    function locate(found) {
      const typed = path();
      const request = ++latest;
      daemon
        .rpc(FIND, {
          name: found.file.name,
          size: found.file.size,
          directories: [...(typed ? [typed] : []), ...bagDirs()],
        })
        .then(
          (result) => {
            if (request !== latest) return;
            const split = found.splits > 1;
            pathInput.value = split ? directoryOf(result.path) : result.path;
            readPath({ keep: split });
          },
          () => {
            if (request !== latest || found.topics) return;
            failed(
              `${found.name}: not found under the bag directories; type the bag's path and read it`,
            );
          },
        );
    }

    async function readFiles(files) {
      const request = ++latest;
      let found;
      try {
        found = await bagFile.readTopics(files, model.parseBagMetadata);
      } catch (error) {
        if (request === latest) failed(error.message);
        return;
      }
      if (request !== latest) return;
      if (found.topics) setBag(found.topics, found.name);
      else if (finds()) {
        topics = null;
        note = `${found.name}: looking for it under the bag directories`;
        state.classList.remove("blocked");
        changed();
      } else failed(bagFile.opaqueNote(found.name));
      if (finds()) locate(found);
    }

    // A dropped path is read through the daemon; dropped files give the
    // topics of their metadata, else of the MCAP files among them.
    async function readDrop(dataTransfer) {
      const drop = await bagFile.dropped(dataTransfer);
      if (drop.path) {
        pathInput.value = drop.path;
        if (reads()) {
          readPath();
          return;
        }
      }
      if (drop.files.length) await readFiles(drop.files);
      else if (drop.path) changed();
      else failed("the drop holds no bag file, metadata.yaml or path");
    }

    function render() {
      read.hidden = !daemon.health || !daemon.offers(READ);
      browse.hidden = read.hidden || !daemon.offers("fs.list");
      browse.disabled = !daemon.connected;
      read.disabled = !daemon.connected || !path();
      // A bag read from a file names no path for the replay to open.
      const pathless = !!topics && !path();
      pathInput.classList.toggle(
        "blocked",
        pathless || (!!path() && !bagFile.isAbsolute(path())),
      );
      if (note) state.textContent = note;
      else
        state.textContent = topics
          ? `${name}: ${topics.size} topics${pathless ? ", path not given" : ""}`
          : "no bag read";
      state.title = state.textContent;
    }

    open.addEventListener("click", () => fileInput.click());
    browse.addEventListener("click", () =>
      picker.open(path() || bagDirs()[0] || "", (chosen) => {
        pathInput.value = chosen;
        readPath();
      }),
    );
    fileInput.addEventListener("change", () => {
      const files = [...fileInput.files];
      fileInput.value = "";
      if (files.length) readFiles(files);
    });
    read.addEventListener("click", () => readPath());
    pathInput.addEventListener("input", changed);
    // Drags entered minus drags left: children fire their own pairs.
    let dragDepth = 0;
    const dropping = (on) => dropTarget.classList.toggle("replay-dropping", on);
    dropTarget.addEventListener("dragenter", (event) => {
      event.preventDefault();
      dragDepth += 1;
      dropping(true);
    });
    dropTarget.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });
    dropTarget.addEventListener("dragleave", () => {
      dragDepth = Math.max(0, dragDepth - 1);
      dropping(dragDepth > 0);
    });
    dropTarget.addEventListener("drop", (event) => {
      event.preventDefault();
      dragDepth = 0;
      dropping(false);
      readDrop(event.dataTransfer).catch((error) => failed(error.message));
    });

    return {
      element: root,
      topics: () => topics,
      name: () => name,
      path,
      // What the path lacks for a replay to open the bag, or "".
      blocker() {
        if (!path())
          return topics
            ? "Type the path of the bag in the Bag field: a file gives none."
            : "Give the path of the bag.";
        return bagFile.isAbsolute(path())
          ? ""
          : "The bag path must be absolute.";
      },
      // Takes the path of a setup: read through the daemon, else no bag.
      show(next) {
        pathInput.value = next || "";
        if (path() && reads()) readPath();
        else {
          latest += 1;
          topics = null;
          note = "";
          state.classList.remove("blocked");
          changed();
        }
      },
      render,
    };
  }

  replay.bagSource = bagSource;
})();
