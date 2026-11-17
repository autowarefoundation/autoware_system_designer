// Path selector: a popup that browses the daemon's file system (`fs.list`)
// one directory at a time. A "dir" picker selects the directory shown; a "bag"
// picker selects a rosbag directory or one of its storage or metadata files.
(function () {
  const SD = window.SD;
  const { element, popup } = SD.widgets;
  const BAG_FILE = /\.(mcap|db3)$|^metadata\.ya?ml$/i;

  function formatSize(bytes) {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`;
  }

  // `kind` is "dir" or "bag"; `open(start, onPick)` shows the directory at
  // `start` (its nearest existing one, else home) and calls `onPick(path)`
  // with the path selected.
  function pathPicker({ title, kind = "dir" }) {
    const box = popup(title);
    box.element.classList.add("path-picker-overlay");
    const up = element("button", "panel-button", "Up");
    const home = element("button", "panel-button", "Home");
    const path = element("input", "panel-search path-picker-path");
    path.type = "text";
    path.spellcheck = false;
    path.setAttribute("aria-label", "Directory");
    const hiddenBox = document.createElement("input");
    hiddenBox.type = "checkbox";
    const hiddenLabel = element("label", "path-picker-hidden", "Hidden");
    hiddenLabel.prepend(hiddenBox);
    const bar = element("div", "path-picker-bar");
    bar.append(up, home, path, hiddenLabel);
    const list = element("div", "path-picker-list");
    const error = element("div", "panel-banner panel-banner-error");
    error.hidden = true;
    const chosen = element("code", "path-picker-chosen");
    const cancel = element("button", "panel-button", "Cancel");
    const select = element("button", "panel-button primary", "Select");
    [up, home, cancel, select].forEach((button) => (button.type = "button"));
    const foot = element("div", "path-picker-foot");
    foot.append(chosen, cancel, select);
    box.body.classList.add("path-picker");
    box.body.append(bar, error, list, foot);

    let listing = null;
    let picked = "";
    let onPick = null;

    function choose(value) {
      picked = value;
      chosen.textContent = value || (kind === "bag" ? "Choose a bag" : "");
      select.disabled = !value;
      list.querySelectorAll(".path-picker-item").forEach((row) => {
        row.classList.toggle("selected", row.dataset.path === value);
      });
    }

    function join(dir, name) {
      return dir.endsWith("/") ? `${dir}${name}` : `${dir}/${name}`;
    }

    function render() {
      path.value = listing.path;
      up.disabled = !listing.parent;
      const rows = listing.entries
        .filter((entry) => entry.dir || BAG_FILE.test(entry.name))
        .map((entry) => {
          const full = join(listing.path, entry.name);
          const row = element(
            "div",
            `path-picker-item${entry.dir ? " dir" : " file"}${entry.bag ? " bag" : ""}`,
          );
          row.dataset.path = full;
          row.tabIndex = 0;
          row.append(
            element("span", "path-picker-icon", entry.dir ? "▸" : "·"),
            element("span", "path-picker-name", entry.name),
            element(
              "span",
              "path-picker-note",
              entry.bag ? "rosbag" : entry.dir ? "" : formatSize(entry.size),
            ),
          );
          // A directory opens on a click; a bag or a bag file is chosen, and
          // opens or is selected on a double click.
          const pickable = kind === "bag" && (entry.bag || !entry.dir);
          row.addEventListener("click", () => {
            if (pickable) choose(full);
            else load(full);
          });
          row.addEventListener("dblclick", () => {
            if (!entry.dir) finish(full);
            else load(full);
          });
          row.addEventListener("keydown", (event) => {
            if (event.key === "Enter") (entry.dir ? load : finish)(full);
          });
          return row;
        });
      list.replaceChildren(
        ...(rows.length
          ? rows
          : [element("p", "panel-empty", "No subdirectory here.")]),
      );
      if (listing.truncated)
        list.appendChild(
          element("p", "panel-note", "Only the first entries are listed."),
        );
      if (kind === "dir") choose(listing.path);
      else choose(listing.bag ? listing.path : "");
    }

    // Latest listing request; the answer of an earlier one is dropped.
    let loads = 0;

    function load(target) {
      const request = ++loads;
      return SD.daemon
        .rpc("fs.list", {
          path: target || "",
          files: kind === "bag",
          hidden: hiddenBox.checked,
        })
        .then(
          (result) => {
            if (request !== loads) return;
            listing = result;
            error.hidden = true;
            render();
          },
          (err) => {
            if (request !== loads) return;
            error.textContent = err.message;
            error.hidden = false;
          },
        );
    }

    function finish(value) {
      if (!value) return;
      box.close();
      onPick?.(value);
    }

    up.addEventListener("click", () => listing?.parent && load(listing.parent));
    home.addEventListener("click", () => load(listing?.home || ""));
    path.addEventListener("keydown", (event) => {
      if (event.key === "Enter") load(path.value.trim());
    });
    hiddenBox.addEventListener("change", () => load(listing?.path || ""));
    cancel.addEventListener("click", box.close);
    select.addEventListener("click", () => finish(picked));

    return {
      open(start, pick) {
        onPick = pick;
        choose("");
        box.open();
        load(start);
      },
    };
  }

  SD.widgets.pathPicker = pathPicker;
})();
