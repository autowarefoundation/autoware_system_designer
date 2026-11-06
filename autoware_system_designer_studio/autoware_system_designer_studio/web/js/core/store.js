// Workbench selection shared by the top bar, the rail panels and the canvas.
// Every key mirrors a URL parameter, so a link reopens the same view.
(function () {
  const SD = window.SD;
  const state = {};
  const urlKeys = new Set();

  function readUrl(key) {
    return new URLSearchParams(window.location.search).get(key);
  }

  function writeUrl() {
    const url = new URL(window.location.href);
    urlKeys.forEach((key) => {
      const value = state[key];
      if (value) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    });
    history.replaceState(null, "", url.toString());
  }

  // Declares a key; the URL value wins over the default when it passes `accept`.
  function define(key, fallback, accept = () => true) {
    const fromUrl = readUrl(key);
    state[key] = fromUrl !== null && accept(fromUrl) ? fromUrl : fallback;
    urlKeys.add(key);
  }

  function get(key) {
    return state[key];
  }

  // Emits "selection" with the changed key; unchanged values emit nothing.
  function set(key, value) {
    if (state[key] === value) return;
    const previous = state[key];
    state[key] = value;
    writeUrl();
    SD.emit("selection", { key, value, previous });
  }

  function onChange(key, handler) {
    return SD.on("selection", (change) => {
      if (change.key === key) handler(change.value, change.previous);
    });
  }

  SD.store = { define, get, set, onChange, syncUrl: writeUrl };
})();
