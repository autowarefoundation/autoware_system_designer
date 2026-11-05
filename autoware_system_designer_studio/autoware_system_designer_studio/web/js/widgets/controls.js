// Building blocks the activities share: elements, side-panel sections and
// lists, and controls bound to the workbench selection.
(function () {
  const SD = window.SD;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Side-panel group under a title.
  function section(title, ...children) {
    const group = element("div", "panel-group");
    group.appendChild(element("div", "panel-section-title", title));
    group.append(...children);
    return group;
  }

  // Single-choice list of [value, label] pairs; `onSelect(value)` runs on a click, `mark` shows the choice.
  function list(items, onSelect) {
    const group = element("div", "panel-list");
    items.forEach(([value, label]) => {
      const item = element("button", "panel-list-item", label);
      item.type = "button";
      item.dataset.value = value;
      item.addEventListener("click", () => onSelect(value));
      group.appendChild(item);
    });
    return {
      element: group,
      mark(value) {
        group.querySelectorAll(".panel-list-item").forEach((item) => {
          item.classList.toggle("active", item.dataset.value === value);
        });
      },
    };
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

  SD.widgets = { element, section, list, boundSelect, setOptions, field, segmented, copyButton };
})();
