// Launch: the Control view's bar of the store's selected entity, the one place
// that acts on a launched member. Actions address one process by its exact
// member path: a node outside a container. A composable node links to its
// container instead, and a module or namespace has none. Each action is offered
// in the member states it applies to, while the session of the shown mode is
// connected; each asks for a confirmation in the bar, dropped once the
// selection or the session changes, or by `cancel`. The runtime's
// operation-mode guard decides; a refusal shows in the bar.
(function () {
  const SD = window.SD;
  const { element } = SD.widgets;
  const launch = (SD.activities.launch ||= {});

  // [action, label, member states it applies to].
  const ACTIONS = [
    ["stop", "Stop", ["running", "respawning"]],
    ["restart", "Restart", ["running", "stopped"]],
    ["kill", "Kill", ["running"]],
  ];
  const HINT = "Select a node on the map to stop, restart or kill its process.";

  function button(label, className, onClick) {
    const node = element("button", `panel-button ${className}`.trim(), label);
    node.type = "button";
    node.addEventListener("click", onClick);
    return node;
  }

  // `entityOf(key)` gives the loaded entity of a selection key, `containerOf(entity)`
  // the container entity of a composable node.
  function memberActions(config, store, { entityOf, containerOf }) {
    const daemon = SD.daemon;
    const { members, sessions, restartsOf } = launch.scope(
      config.systemId,
      () => store.get("mode"),
    );
    const bar = element("div", "member-bar");
    // The action awaiting its confirmation: { action, path, job }.
    let confirming = null;
    // Member paths with a request in flight.
    const pending = new Set();
    // The last refusal or error of a request: { path, text }.
    let note = null;

    // Target of the selection: the node, its member record and serving session.
    function target(entity) {
      const member = members.get(entity.member_path);
      const session = member && sessions.get(member.unit);
      return { member, session, state: member?.record?.state || "" };
    }

    function live(session) {
      return !!session && session.state !== "exited" && session.connected;
    }

    function act(entity, action) {
      const { member } = target(entity);
      if (!member) return;
      const path = entity.member_path;
      confirming = null;
      note = null;
      pending.add(path);
      render();
      daemon
        .rpc(`launch.${action}`, {
          compute_unit: member.unit,
          pattern: path,
          exact: true,
        })
        .catch((error) => {
          note = { path, text: error.message };
        })
        .finally(() => {
          pending.delete(path);
          render();
        });
    }

    function pathText(text) {
      const node = element("span", "member-bar-path", text);
      node.title = text;
      return node;
    }

    function renderNode(entity) {
      const { member, session, state } = target(entity);
      const path = entity.member_path;
      const { token, label } = launch.memberState(entity, members);
      const badge = element("span", `member-state ${token}`);
      badge.append(SD.RunState.mark(token), element("span", "", label));
      bar.append(pathText(path));
      if (member)
        bar.appendChild(element("span", "member-bar-unit", member.unit));
      bar.appendChild(badge);
      const counts = member && restartsOf(path);
      if (counts?.restarts) {
        const restarts = element(
          "span",
          "member-restarts",
          `restarted ×${counts.restarts}`,
        );
        restarts.title = `${counts.respawns} respawned`;
        bar.appendChild(restarts);
      }
      if (
        confirming &&
        (confirming.path !== path || confirming.job !== session?.job)
      )
        confirming = null;
      // The confirmation sits apart from the actions, which stay in place and
      // disabled meanwhile, so a second click on an action never confirms it.
      if (confirming) {
        const [, actionLabel] = ACTIONS.find(([a]) => a === confirming.action);
        const action = confirming.action;
        const confirm = element("span", "member-bar-confirm");
        confirm.append(
          element(
            "span",
            "member-bar-question",
            `${actionLabel} this process?`,
          ),
          button(`Confirm ${action}`, "danger", () => act(entity, action)),
          button("Cancel", "", () => {
            confirming = null;
            render();
          }),
        );
        bar.appendChild(confirm);
      }
      const actions = element("span", "member-bar-actions");
      const busy = pending.has(path);
      ACTIONS.forEach(([action, actionLabel, states]) => {
        const node = button(actionLabel, "", () => {
          confirming = { action, path, job: session.job };
          render();
        });
        node.disabled =
          !!confirming || busy || !live(session) || !states.includes(state);
        node.classList.toggle("armed", confirming?.action === action);
        actions.appendChild(node);
      });
      bar.appendChild(actions);
      if (note?.path === path) {
        const text = element("span", "member-bar-note", note.text);
        text.title = note.text;
        bar.appendChild(text);
      }
      if (session?.control?.policy === "guard")
        bar.title =
          "The runtime refuses actions while the operation mode is autonomous or in transition.";
    }

    function renderComposable(entity) {
      bar.append(
        pathText(entity.member_path),
        element("span", "member-bar-hint", "composable node in"),
      );
      const container = containerOf(entity);
      if (!container) {
        bar.appendChild(pathText(entity.container_path));
        return;
      }
      const link = button(entity.container_path, "member-bar-link", () =>
        store.set("entity", container.key),
      );
      link.title = "Select the container, the process that holds this node";
      bar.appendChild(link);
    }

    function render() {
      bar.replaceChildren();
      bar.title = "";
      bar.hidden = !daemon.offers("launch.start");
      if (bar.hidden) return;
      const key = store.get("entity");
      const entity = key && entityOf(key);
      if (!entity) {
        confirming = null;
        bar.appendChild(element("span", "member-bar-hint", HINT));
        return;
      }
      if (entity.entity_type !== "node") {
        confirming = null;
        bar.append(
          pathText(entity.path),
          element(
            "span",
            "member-bar-hint",
            "A module has no process of its own; select one of its nodes.",
          ),
        );
        return;
      }
      if (entity.container_path) {
        confirming = null;
        renderComposable(entity);
        return;
      }
      renderNode(entity);
    }

    store.onChange("entity", () => {
      confirming = null;
      note = null;
      render();
    });
    store.onChange("mode", () => {
      confirming = null;
      render();
    });
    SD.on("launch.member", render);
    SD.on("launch.sessions", render);
    render();

    // Drops the confirmation awaiting an answer; false when none was.
    function cancel() {
      if (!confirming) return false;
      confirming = null;
      render();
      return true;
    }

    return { element: bar, render, cancel };
  }

  launch.memberActions = memberActions;
})();
