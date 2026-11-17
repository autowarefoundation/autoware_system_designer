// Replay: the lists of the boundary and the code blocks of the commands:
// a table, the check list of the outputs, the list of the inputs with the
// source of each, a titled block and a block of text to copy.
(function () {
  const SD = window.SD;
  const { element, copyButton } = SD.widgets;
  const replay = (SD.activities.replay ||= {});

  function table(columns) {
    const node = element("table", "replay-table");
    const head = node.createTHead().insertRow();
    columns.forEach((title) => head.appendChild(element("th", "", title)));
    const body = node.createTBody();
    return {
      element: node,
      clear: () => body.replaceChildren(),
      row(cells, className) {
        const row = body.insertRow();
        if (className) row.className = className;
        cells.forEach((cell) => {
          const td = row.insertCell();
          if (cell instanceof Node) td.appendChild(cell);
          else td.textContent = cell ?? "";
        });
        return row;
      },
    };
  }

  function topicCell(topic, type) {
    const cell = element("div", "replay-topic");
    cell.append(
      element("span", "replay-topic-name", topic),
      element("span", "replay-topic-type", type),
    );
    return cell;
  }

  // Check list of topics under a head that clears every topic it may, or
  // checks them all once none is; `toggle(topic, on)` takes each change.
  function topicSelector(title, toggle) {
    const node = element("div", "replay-selector");
    const all = document.createElement("input");
    all.type = "checkbox";
    const count = element("span", "replay-block-count");
    const head = element("label", "replay-selector-head");
    head.append(all, element("span", "", title), count);
    const list = element("div", "replay-selector-list");
    node.append(head, list);
    let items = [];
    all.addEventListener("change", () => {
      const on = !items.some((item) => item.checked && !item.fixed);
      items
        .filter((item) => !item.fixed && item.checked !== on)
        .forEach((item) => toggle(item.topic, on));
    });
    return {
      element: node,
      // `next` is [{ topic, note, level, checked, fixed }].
      set(next) {
        items = next;
        const checked = items.filter((item) => item.checked).length;
        all.checked = items.length > 0 && checked === items.length;
        all.indeterminate = checked > 0 && !all.checked;
        all.disabled = items.every((item) => item.fixed);
        count.textContent = `${checked} of ${items.length}`;
        list.replaceChildren(
          ...items.map((item) => {
            const box = document.createElement("input");
            box.type = "checkbox";
            box.checked = item.checked;
            box.disabled = !!item.fixed;
            box.addEventListener("change", () =>
              toggle(item.topic, box.checked),
            );
            const row = element("label", `replay-selector-item ${item.level}`);
            row.append(
              box,
              element("span", "replay-topic-name", item.topic),
              element("span", "replay-selector-note", item.note),
            );
            return row;
          }),
        );
      },
    };
  }

  // Rank of an input status in the inputs list: unresolved inputs head it.
  const INPUT_RANK = [
    "missing",
    "type",
    "unpublished",
    "unknown",
    "accepted",
    "remapped",
    "fed",
  ];
  // Statuses the head's action accepts unfed.
  const UNRESOLVED = ["missing", "unpublished"];
  // Statuses whose rows always show the input's type and ports.
  const ISSUES = ["missing", "type", "unpublished"];

  // Type, bag record and nodes of a boundary input, a line each.
  function inputDetail(input) {
    const node = element("dl", "replay-input-detail");
    const entry = (term, values, className = "") => {
      const list = element("dd", className);
      values.forEach((value) => list.appendChild(element("span", "", value)));
      node.append(element("dt", "", term), list);
    };
    entry("type", [input.type || "any"]);
    if (input.status === "type")
      entry("in the bag", [input.recordedType], "replay-input-conflict");
    else if (input.status === "remapped")
      entry("in the bag", [input.recordedName]);
    entry(
      "published by",
      input.publishers.length
        ? input.publishers
        : ["no publisher in the design"],
      input.publishers.length ? "" : "replay-input-none",
    );
    entry("subscribed by", input.subscribers);
    return node;
  }

  // List of the boundary inputs, each with a pulldown of its sources;
  // `choices(input)` gives them, `choose(input, value)` takes a pick,
  // `acceptAll(inputs)` the head's action and `select(topic)` a click on a
  // row, null for the selected one. Rows of an issue and the selected row
  // show the input's detail.
  function inputSelector(choices, choose, acceptAll, select) {
    const node = element("div", "replay-selector");
    const count = element("span", "replay-block-count");
    const acceptButton = element("button", "panel-button", "Accept unfed");
    acceptButton.type = "button";
    acceptButton.title =
      "Accept every input that is not in the bag unfed; the pre-flight passes without them";
    const head = element("div", "replay-selector-head");
    head.append(element("span", "", "Inputs"), count, acceptButton);
    const list = element("div", "replay-selector-list");
    node.append(head, list);
    let unresolved = [];
    acceptButton.addEventListener("click", () => acceptAll(unresolved));

    function picker(input) {
      const options = choices(input);
      // An input fed by the one topic of its name has nothing to pick.
      if (options.length < 2)
        return element(
          "span",
          "replay-selector-source",
          options[0]?.value === input.topic ? "" : options[0]?.label || "",
        );
      const select = document.createElement("select");
      select.className = "replay-selector-source";
      select.setAttribute("aria-label", `Source of ${input.topic}`);
      options.forEach((choice, index) => {
        const option = document.createElement("option");
        option.value = String(index);
        option.textContent = choice.note
          ? `${choice.label} (${choice.note})`
          : choice.label;
        option.selected = choice.current;
        select.appendChild(option);
      });
      if (!options.some((choice) => choice.current)) select.selectedIndex = -1;
      select.addEventListener("change", () =>
        choose(input, options[Number(select.value)].value),
      );
      return select;
    }

    return {
      element: node,
      // `inputs` is the matched inputs, `notes` the text of each status,
      // `selected` the topic of the selected input or null.
      set(inputs, notes, selected) {
        const ranked = inputs
          .map((input, index) => ({ input, index }))
          .sort(
            (a, b) =>
              INPUT_RANK.indexOf(a.input.status) -
                INPUT_RANK.indexOf(b.input.status) || a.index - b.index,
          )
          .map(({ input }) => input);
        unresolved = ranked.filter((input) =>
          UNRESOLVED.includes(input.status),
        );
        const issues = ranked.filter(
          (input) => INPUT_RANK.indexOf(input.status) < 4,
        ).length;
        count.textContent =
          `${ranked.length}` + (issues ? `, ${issues} unresolved` : "");
        acceptButton.hidden = !unresolved.length;
        list.replaceChildren(
          ...ranked.map((input) => {
            const chosen = input.topic === selected;
            const row = element(
              "div",
              `replay-selector-item replay-input-row ${input.status}` +
                (chosen ? " selected" : ""),
            );
            const line = element("div", "replay-input-line");
            line.append(
              element("span", "replay-topic-name", input.topic),
              element("span", "replay-selector-note", notes[input.status]),
              picker(input),
            );
            row.appendChild(line);
            if (chosen || ISSUES.includes(input.status))
              row.appendChild(inputDetail(input));
            row.tabIndex = 0;
            row.setAttribute("aria-selected", String(chosen));
            row.title = chosen
              ? "Click to clear the selection"
              : "Click to show the input in the diagram";
            const toggle = () => select(chosen ? null : input.topic);
            row.addEventListener("click", (event) => {
              if (event.target.closest("select")) return;
              toggle();
            });
            row.addEventListener("keydown", (event) => {
              if (event.target !== row) return;
              if (event.key !== "Enter" && event.key !== " ") return;
              event.preventDefault();
              toggle();
            });
            return row;
          }),
        );
      },
    };
  }

  function block(title, note) {
    const node = element("section", "replay-block");
    const head = element("div", "replay-block-head");
    const count = element("span", "replay-block-count");
    head.append(element("h3", "", title), count);
    node.appendChild(head);
    if (note) node.appendChild(element("p", "panel-note", note));
    return { element: node, count };
  }

  function codeBlock(title) {
    const node = element("div", "replay-code");
    const head = element("div", "replay-code-head");
    const actions = element("div", "replay-code-actions");
    head.append(element("span", "", title), actions);
    const pre = element("pre");
    node.append(head, pre);
    return {
      element: node,
      actions,
      set(text, ...buttons) {
        pre.textContent = text;
        actions.replaceChildren(copyButton(text), ...buttons);
      },
    };
  }

  function checkbox(checked, onChange, disabled = false) {
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = checked;
    box.disabled = disabled;
    box.addEventListener("change", () => onChange(box.checked));
    return box;
  }

  replay.lists = {
    table,
    topicCell,
    topicSelector,
    inputSelector,
    block,
    codeBlock,
    checkbox,
  };
})();
