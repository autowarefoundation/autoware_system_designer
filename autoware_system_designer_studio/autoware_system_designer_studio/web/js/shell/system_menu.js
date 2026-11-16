// System selector of the top bar: the shown system as a button, and the site's
// systems grouped by package in its popover. A system opens on the activity
// shown here.
(function () {
  const SD = window.SD;

  const el = (id) => document.getElementById(id);

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function init(config) {
    const button = el("system-menu-button");
    const menu = el("system-menu");
    const own = config.systems.find((s) => s.id === config.systemId);
    el("system-name").textContent = config.deploymentName;
    el("system-package").textContent = config.packageName;
    if (own) el("system-icon").src = config.siteRoot + own.icon;

    const links = [];
    let lastPackage = null;
    config.systems.forEach((system) => {
      if (system.package !== lastPackage) {
        lastPackage = system.package;
        menu.appendChild(node("div", "system-menu-package", system.package));
      }
      const link = node("a", "system-menu-item");
      link.setAttribute("role", "menuitem");
      const icon = node("img", "system-icon");
      icon.alt = "";
      icon.src = config.siteRoot + system.icon;
      link.append(icon, node("span", "system-menu-name", system.name));
      if (system.id === config.systemId) {
        link.classList.add("current");
        link.setAttribute("aria-current", "page");
      }
      menu.appendChild(link);
      links.push([link, system]);
    });

    SD.popover(button, menu, {
      onOpen() {
        const activity = SD.store.get("activity");
        links.forEach(([link, system]) => {
          link.href = `${config.siteRoot}${system.page}?activity=${encodeURIComponent(activity)}`;
        });
      },
    });
    SD.storage.set(SD.LAST_SYSTEM_KEY, config.systemId);
  }

  SD.systemMenu = { init };
})();
