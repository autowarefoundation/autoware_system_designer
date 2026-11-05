// Building blocks the activities share: elements, controls bound to the
// workbench selection, and the terminal view of a log.
(function () {
  const SD = window.SD;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Select mirroring the store key `key`; options are [value, label] pairs.
  function boundSelect(key, options) {
    const select = document.createElement("select");
    setOptions(select, options);
    select.value = SD.store.get(key);
    select.addEventListener("change", () => SD.store.set(key, select.value));
    SD.store.onChange(key, (value) => {
      select.value = value;
    });
    return select;
  }

  function setOptions(select, options) {
    select.replaceChildren(
      ...options.map(([value, label]) => {
        const opt = element("option", "", label);
        opt.value = value;
        return opt;
      }),
    );
  }

  // Labelled side-panel field around `control`.
  function field(label, control) {
    const wrapper = element("label", "panel-field", label);
    wrapper.appendChild(control);
    return wrapper;
  }

  // Segmented switch over [value, label] pairs; `onSelect(value)` runs on a click.
  function segmented(options, onSelect) {
    const group = element("div", "segmented");
    options.forEach(([value, label]) => {
      const button = element("button", "", label);
      button.type = "button";
      button.dataset.value = value;
      button.addEventListener("click", () => onSelect(value));
      group.appendChild(button);
    });
    return {
      element: group,
      mark(value) {
        group.querySelectorAll("button").forEach((button) => {
          button.classList.toggle("active", button.dataset.value === value);
        });
      },
    };
  }

  function copyButton(text) {
    const button = element("button", "copy-btn", "Copy");
    button.type = "button";
    button.title = text;
    button.addEventListener("click", () => {
      navigator.clipboard?.writeText(text).then(() => {
        button.textContent = "Copied";
        button.classList.add("copied");
        setTimeout(() => {
          button.textContent = "Copy";
          button.classList.remove("copied");
        }, 1500);
      });
    });
    return button;
  }

  // Log view keeping the last `maxLines` lines; it follows new output while scrolled to the end.
  function terminal(placeholder, maxLines = 5000) {
    const log = element("pre", "terminal-log");
    let lineCount = 0;

    function clear(text = placeholder) {
      log.replaceChildren(element("span", "terminal-placeholder", text));
      lineCount = 0;
    }

    function append(lines) {
      if (!lines.length) return;
      log.querySelector(".terminal-placeholder")?.remove();
      const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 4;
      lines.forEach((line) => log.appendChild(document.createTextNode(`${line}\n`)));
      lineCount += lines.length;
      while (lineCount > maxLines) {
        log.firstChild.remove();
        lineCount -= 1;
      }
      if (atBottom) log.scrollTop = log.scrollHeight;
    }

    clear();
    return { element: log, append, clear };
  }

  SD.widgets = { element, boundSelect, setOptions, field, segmented, copyButton, terminal };
})();
