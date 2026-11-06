// Shared page core: the SD namespace every studio page script attaches to,
// a page-wide event bus, and the persisted theme preference.
(function () {
  const SD = window.SD || {};
  const bus = new EventTarget();

  // Page-wide events: "themechange" today; panels and overlays add their own.
  SD.on = function (type, handler) {
    const listener = (event) => handler(event.detail);
    bus.addEventListener(type, listener);
    return () => bus.removeEventListener(type, listener);
  };

  SD.emit = function (type, detail) {
    bus.dispatchEvent(new CustomEvent(type, { detail }));
  };

  // Preference storage that tolerates browsers refusing localStorage.
  SD.storage = {
    get(key) {
      try {
        return localStorage.getItem(key);
      } catch (error) {
        return null;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(key, value);
      } catch (error) {
        // The preference lasts for this page only.
      }
    },
  };

  // Theme shared by every page through one stored preference; the system
  // preference applies until the viewer picks one.
  const THEME_KEY = "theme";
  const TOGGLE_ID = "dark-mode-switch";

  function systemTheme() {
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function current() {
    return document.documentElement.getAttribute("data-theme") || "light";
  }

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    document
      .getElementById(TOGGLE_ID)
      ?.classList.toggle("active", theme === "dark");
  }

  function toggle() {
    const next = current() === "dark" ? "light" : "dark";
    apply(next);
    SD.storage.set(THEME_KEY, next);
    SD.emit("themechange", { theme: next });
  }

  // Applies the preference and wires the page's toggle switch, when present.
  function init() {
    apply(SD.storage.get(THEME_KEY) || systemTheme());
    document.getElementById(TOGGLE_ID)?.addEventListener("click", toggle);
  }

  SD.theme = { init, apply, toggle, current };
  window.SD = SD;
})();
