/**
 * Shared theme (dark/light) logic. Persists preference in localStorage so
 * all pages (systems index, deployment overview, launch commands) keep the same mode.
 */
(function () {
  function getSystemThemePreference() {
    return window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function getSavedThemePreference() {
    return localStorage.getItem("theme");
  }

  function saveThemePreference(theme) {
    localStorage.setItem("theme", theme);
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    var toggle = document.getElementById("dark-mode-switch");
    if (toggle) {
      toggle.classList.toggle("active", theme === "dark");
    }
  }

  function toggleDarkMode() {
    var currentTheme =
      document.documentElement.getAttribute("data-theme") || "light";
    var newTheme = currentTheme === "dark" ? "light" : "dark";
    applyTheme(newTheme);
    saveThemePreference(newTheme);
    if (typeof window.__onThemeChange === "function") {
      window.__onThemeChange();
    }
  }

  function initializeTheme() {
    var savedTheme = getSavedThemePreference();
    var systemTheme = getSystemThemePreference();
    var theme = savedTheme || systemTheme;
    applyTheme(theme);
  }

  window.applyTheme = applyTheme;
  window.toggleDarkMode = toggleDarkMode;
  window.initializeTheme = initializeTheme;
  window.getSavedThemePreference = getSavedThemePreference;
})();
