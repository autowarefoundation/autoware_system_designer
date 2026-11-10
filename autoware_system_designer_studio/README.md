# `autoware_system_designer_studio` — views and control over exported systems

The studio draws every view from the designer's export artifacts
(`<pkg>/share/<pkg>/exports/<system>/{deployment.json, system_structure/, launcher/, latency/}`) and the
runtime's measurement records. The designer build writes no pages; the studio renders them, live
from a loopback daemon or packed to a directory on demand.

| Package                            | Role                                                     |
| ---------------------------------- | -------------------------------------------------------- |
| `autoware_system_designer`         | design → exports                                         |
| `autoware_system_designer_runtime` | runs exports, writes measurement records                 |
| `autoware_system_designer_studio`  | views, build and launch control over exports and records |

The studio reads exports by file contract through `autoware_system_designer_runtime.export_tree` and
`autoware_system_designer_runtime.records`; it imports nothing from the designer.

## Pack

```bash
autoware-system-designer-studio pack -o /tmp/studio \
    [--install-root install] [--system TypeAlpha] [--record-dir DIR]
xdg-open /tmp/studio/index.html
```

`pack` writes the site of the selected systems (default: all) with its bundle. Data travels as
scripts, so the directory opens from `file://` and can be copied anywhere. Pack only rewrites a
directory it wrote itself (marked by `.autoware_system_designer_studio`) or an empty one.

## Site layout

```text
index.html                                         systems index
assets/{css,js}/...                                web bundle
systems/<package>/<system>/index.html              workbench; ?activity=explore|launch|build opens that activity
systems/<package>/<system>/config.js               workbench config
systems/<package>/<system>/data/<mode>.js          system_structure/<mode>.json with vis guides attached
systems/<package>/<system>/data/<mode>_latency.js  newest latency record of the mode, else the exported reference
```

Links are relative, so `serve` and `pack` produce the same pages.

## Web bundle

`web/` holds plain scripts (no build step, `file://` compatible) under the `SD` namespace and one
stylesheet per area; `pages/assets.py` declares the load order of each page.

| Path                               | Role                                                                                   |
| ---------------------------------- | -------------------------------------------------------------------------------------- |
| `js/core/`                         | `SD` namespace, page events, theme, editor links, selection store, daemon client       |
| `js/diagrams/`                     | `DiagramBase`, the three diagram modules (registered on `SD.diagrams`)                 |
| `js/inspector/`                    | info panel of the selected element                                                     |
| `js/shell/`                        | workbench frame: activity registry and rail, panel layout, status bar                  |
| `js/widgets/`                      | building blocks activities share: controls, main view, terminal, namespace tree, marks |
| `js/activities/<id>/`              | one directory per rail activity: its parts, then `index.js` registering it             |
| `js/pages/`                        | page drivers                                                                           |
| `css/tokens.css`                   | colours (light/dark) and run-state tokens; every other stylesheet reads them           |
| `css/shell.css`, `css/widgets.css` | the frame and the shared widgets                                                       |
| `css/activities/<id>.css`          | the activity's own styles                                                              |

Each rail item of the workbench is an activity owning one view per slot, shown and hidden together:
its options in the side panel, its content in the main area and, when it selects elements, the
inspector. The top bar holds the system and the mode, which every activity reads; other selections
live in the activity that uses them, as store keys mirrored in the URL.

| Activity | Side panel                                                      | Main area                                            |
| -------- | --------------------------------------------------------------- | ---------------------------------------------------- |
| Explore  | diagram list; entity search; live state of the launched members | diagram canvas; inspector shows the selected element |
| Launch   | view list, deploy, launch options, launch commands popup, RViz  | Control, Logs or Topics view of the mode (`?view=`)  |
| Build    | target, compute unit, start; job list (daemon)                  | log of the selected job (`?job=`)                    |

Launch has three main views, chosen from the list in its side panel. Control is the treemap of the
mode, every block coloured by its live member state and marked, before its name, with the state, a
busy log and, on a node, its restart count. Launch and Shutdown of each compute unit (and the
measurement window of a session whose runtime preloads the tracer) are in the unit's head bar;
"Measure from launch" opens the window at launch. Every declared unit has its own
section, a head alone when the mode places no node on it; a launcher of an undeclared unit gets the
same controls in a row above the map. A bar between the head and the map holds the selected
entity and the only actions on a launched member: Stop, Restart and Kill of a node outside a
container, each in the member states it applies to while the session of the mode is connected, sent
with `exact` so they reach that member alone; each is confirmed in the bar. A composable
node links to its container there, and a module has no actions. Logs is the member tree beside the
selected member's log, read only. Topics is one row per topic of the mode: the design's publisher,
subscribers and declared rate (the output port's `frequency`, with the system monitor's `warn_rate`
and `error_rate`), beside the publish rate, mean callback time and mean communication time the
runtimes count. A row's status is `silent` (no message), `error` or `slow` (below the declared
error or warn rate, else below 0.8 of the rate), `fast` (above 1.25 of it), `ok`, `once` (declared
to publish once), `intra` (intra-process only, which rcl does not count), `new`, `not seen` or
`offline` (every node of the topic on a unit that is not running). Rows group by the publishing
node's component (first path segment) or namespace, or by the first two segments of the topic's
namespace; the view follows the rates of every connected session of the mode while shown, and
keeps the last rates of a launch that ended until its unit is launched again. A selector beside the
Topics title shows a past run of the mode instead, from the rates record in its log directory, over
the whole run or the last 10 s, 1 min or 5 min up to a sample chosen on a slider. All three
select the store's `entity`, and a double click on a node of the map opens it in Logs.

An activity is one `SD.workbench.registerActivity(def)` call from its `index.js`, with its parts
attached to `SD.activities.<id>` by the scripts before it. `def` names the activity (`id`, `title`,
`icon`, `order`), its daemon needs (`daemon`, `requires`), whether it owns an inspector view
(`inspector`), what it adds to the status bar (`status(store)`), and `mount(slots, ctx)`, called on
first show with `{ side, main, inspector? }` and `{ config, store, daemon }`. `mount` may return
`{ show(), hide() }`: `show` runs on every later show, `hide` when another activity takes over. Adding
an activity is that directory, its stylesheet and one entry in `pages/assets.py` `ACTIVITIES`.

## Serve

`serve` renders the studio from the exports of an install tree per request, over HTTP, together
with a request API and an event stream that build, launch and live monitoring extend. It imports no
ROS module, so its `bin/` copy runs without any setup script sourced.

```bash
autoware-system-designer-studio serve \
    --install-root install \       # default: first COLCON_PREFIX_PATH entry
    --record-dir ~/.ros/autoware_system_designer/records \  # measurement records (default shown)
    --port 8780 \                  # 0 picks a free port
    --workspace . \                # colcon workspace builds run in (default: install parent holding src/)
    --compute-unit main_ecu \      # units local to this daemon (repeatable; default: every onboard unit)
    --env-script install/setup.bash \  # sourced once into the environment launch jobs run in
    --rviz-config autoware.rviz    # opened by rviz.start (default: autoware_launch's autoware.rviz)
```

Open `http://127.0.0.1:8780/`. Pages follow the [site layout](#site-layout) and reflect the exports
as they are at each request, so a finished build shows on reload. The workbench Build activity
starts, follows and cancels builds. Nothing else of the install tree is served.

| Route              | Content                                                                                                      |
| ------------------ | ------------------------------------------------------------------------------------------------------------ |
| `GET /`            | Systems index                                                                                                |
| `GET /<site path>` | Page or data script of the [site layout](#site-layout)                                                       |
| `GET /assets/<f>`  | Web bundle shipped with this package (`css/`, `js/`)                                                         |
| `GET /api/health`  | Daemon status, including `workspace`, `record_root` and `restart_required`                                   |
| `GET /api/systems` | Exported systems: modes, compute units with their status, build scripts per mode, deploy variants, page URLs |
| `GET /api/jobs`    | Jobs, newest first                                                                                           |
| `GET /api/launch`  | Launch sessions with their members                                                                           |
| `GET /api/events`  | Server-Sent Events with daemon-wide sequence numbers as event ids (topics below)                             |
| `POST /api/rpc`    | Request envelope `{"id", "method", "params"}` for any method below                                           |

| Method                                            | Params                                                                             | Result                                       |
| ------------------------------------------------- | ---------------------------------------------------------------------------------- | -------------------------------------------- |
| `health`                                          | -                                                                                  | Daemon status                                |
| `systems.list`                                    | -                                                                                  | Exported systems                             |
| `jobs.list`                                       | -                                                                                  | `{"jobs": [job]}`                            |
| `jobs.get`                                        | `job`, `since` (log seq, default 0), `limit`                                       | `{"job", "log": {"first_seq", "lines"}}`     |
| `jobs.cancel`                                     | `job`                                                                              | job                                          |
| `build.start`                                     | `system` (`<pkg>:<name>`), `target`, `mode`, `compute_unit`                        | job                                          |
| `launch.start`                                    | `system`, `compute_unit`, `mode`, `deploy`, `args`, `policy`, `measure`, `respawn` | session                                      |
| `launch.status`                                   | `compute_unit` (optional)                                                          | `{"sessions": [session]}`, members included  |
| `launch.stop`, `launch.restart`, `launch.kill`    | `compute_unit`, `pattern`, `exact` (optional: the member named `pattern` only)     | `{"members": [path]}` acted on               |
| `launch.follow`                                   | `compute_unit`, `path`, `system` and `mode` (optional: the session of that mode)   | `{"path", "out": [lines], "err": [lines]}`   |
| `launch.unfollow`                                 | `compute_unit`, `path` (optional: every follow), `system` and `mode` (optional)    | `{"released": [path]}`                       |
| `launch.runs`                                     | `system` (optional)                                                                | `{"runs": [run]}`, newest first              |
| `launch.run`                                      | `run`                                                                              | run with `members: [{"path", "slug"}]`       |
| `launch.run_tail`                                 | `run`, `member` (its directory name)                                               | `{"run", "member", "out", "err"}`            |
| `launch.run_rates`                                | `run`, `until_ns`, `span_s` (optional: the span ending at that sample)             | run with `times` and the rates over the span |
| `launch.shutdown`                                 | `compute_unit`                                                                     | `{"shutting_down": true}`                    |
| `measure.start`, `measure.stop`, `measure.status` | `compute_unit`                                                                     | `{"state", "message"}` of the window         |
| `monitor.follow`, `monitor.unfollow`              | `compute_unit`                                                                     | `{"interval_s"}`, `{"released"}`             |
| `monitor.sample`                                  | `compute_unit`, `interval` (seconds, default 1)                                    | rates over the interval                      |
| `rviz.start`                                      | -                                                                                  | job                                          |

| Event topic       | Data                                                                                                   |
| ----------------- | ------------------------------------------------------------------------------------------------------ |
| `hello`           | Sent first on each stream; no replay, so clients resynchronise by request                              |
| `heartbeat`       | `{"time"}`                                                                                             |
| `job`             | Job record on every state change                                                                       |
| `job.log`         | `{"job", "first_seq", "lines"}`, contiguous log lines                                                  |
| `systems.changed` | A build rebuilt an export package; re-read `systems.list`                                              |
| `daemon`          | `{"restart_required": true}`                                                                           |
| `launch`          | Session view (without members) on every change of a launch                                             |
| `member`          | `{"compute_unit", "system", "mode", "path", "state", "detail", "time", "restarts", "respawns"}`        |
| `member.log`      | `{"compute_unit", "path", "stream", "lines"}`, new lines of a followed member                          |
| `member.activity` | `{"compute_unit", "system", "mode", "window", "rates": {path: bytes/s}}`, output growth per window     |
| `monitor`         | `{"compute_unit", "system", "mode", "interval_s", "nodes": [{"node", "inputs", "outputs", "timers"}]}` |
| `records.changed` | `{"system", "mode", "record"}`: a new latency record of the mode is complete                           |

Responses use one envelope: `{"ok": true, "result": ...}` or
`{"ok": false, "error": {"code", "message"}}`, with `id` echoed on `/api/rpc`. Error codes are
stable strings (`bad_request`, `forbidden`, `not_found`, `method_not_allowed`,
`unsupported_media_type`, `payload_too_large`, `unknown_method`, `invalid_params`, `conflict`,
`refused`, `unavailable`, `internal`), each mapped to one HTTP status. Compute unit `status` is
open-ended; current values are `available`, `running` (an available unit launched by this daemon),
`remote_unsupported` (an onboard unit served by another daemon), `offboard` and `unsupported`.

Access rules: the listener binds `127.0.0.1` only; the `Host` header must name it (`127.0.0.1:<port>`
or `localhost:<port>`); API requests the browser marks cross-site are refused; `POST /api/rpc` needs
`Content-Type: application/json` and an `X-System-Designer-Client` header, and a present `Origin`
must be the daemon's own. Request heads are capped at 16 KiB and bodies at 64 KiB.

### Jobs and builds

A job is one supervised child process in its own process group, stdout and stderr merged into a
ring of the last 5000 numbered lines. One job per key runs at a time (`conflict` otherwise); the
last 20 finished jobs stay listed. Cancel sends SIGINT to the group, SIGTERM after 10 s and SIGKILL
5 s later; daemon shutdown cancels every job. Job `state` is `running`, `succeeded`, `failed` or
`cancelled`.

`build.start` runs `colcon build` in the workspace against the served install tree
(`--install-base`, `--merge-install` for a merged tree). All builds share one key, since colcon builds
of one workspace cannot overlap.

| `target`     | Packages                                                                                                       |
| ------------ | -------------------------------------------------------------------------------------------------------------- |
| `deployment` | `--packages-select <pkg>` of the export package (regenerates the export)                                       |
| `workspace`  | `--packages-up-to` the packages of `build_scripts/<mode>/build_<compute_unit>.sh` (default mode: the system's) |

The generated script is parsed, not executed; package names are checked. `compute_unit` must be
available on this daemon. Job `detail` holds `packages` (selection) and `built_packages` (from
colcon's `Starting >>>` lines). When a build rebuilds `autoware_system_designer_studio` or
`autoware_system_designer_runtime` inside the install tree this daemon runs from, the daemon keeps
running its loaded code and reports `restart_required`.

### Launch

`launch.start` runs the designer runtime launcher (`autoware-system-designer-launch`, found on the
launch environment's PATH or under the install tree's `bin/`) on the mode's system structure as a
job, one job key per compute unit, with `--compute-unit`, `--deploy`, one `--arg NAME=VALUE` per
`args` entry, `--measure` and `--respawn` as requested, and `--control <socket>` on a private socket
under `$XDG_RUNTIME_DIR`. The daemon connects to the socket once the runtime serves it, forwards the
runtime's `member`, `member.log` and `member.activity` events and relays `launch.stop|restart|kill`
(a member `pattern`: the exact path, else, without `exact`, every path containing it), `launch.shutdown` and
`measure.*` as requests. A shutdown asked before the channel is up cancels the job. The runtime's own
output is the job's log.

A compute unit runs one launch at a time, and a system one mode at a time across its units: a start
on a running unit, or of another mode while a unit of the system runs, answers `conflict`. The daemon
keeps the last session of each mode of a system on a unit, with its member states, until that mode
is launched there again; requests by `compute_unit` alone address the unit's running session, else
its latest.

Member output is tailed on demand: `launch.follow` asks the runtime to tail one member's `out` and
`err` files and answers with their current tails, after which the member's new lines arrive as
`member.log` events until `launch.unfollow`. The daemon is one runtime client, so follows are shared
by every event stream; they are released once the last stream closes, and a runtime's follows end
with its channel. Once the channel is gone, a follow answers the tails of the member's files in the
runtime's log directory (`log_dir` of the session view, from the runtime's hello), with no lines to
come.

Topic rates are followed the same way: `monitor.follow` counts one more follower of a unit and the
first asks the runtime for its per-node rates (the tracer's counters, see the runtime README), which
arrive as `monitor` events every 2 s; `monitor.unfollow` counts one less and the last releases them.
Every follow ends with the last event stream, and a reconnected channel is followed again.

The Logs view follows the selected node (a composable node through its container) while
it is shown, keeps the followed log once the launcher ended and marks where it ended.

A selector beside the Logs title shows a past run instead of the live sessions. A run is
a directory under the runtime's log base (`/tmp/autoware_system_designer_logs`, and the parents of
the sessions' log directories) holding a `run.json`; a directory without one is not a run. The
daemon lists the runs of the system that no active session is writing, newest first, labelled by
start time, mode, deploy variant and compute unit from the manifest. A run is one runtime, so one
compute unit: members of the other units are dimmed like members without a log. `launch.run` gives
a run's members, the manifest's member paths that have a directory, each with its directory name;
`launch.run_tail` the tails of one directory's `out` and `err` files. The tree keeps the mode's
structure: members the run holds no log for are dimmed, its members outside the mode are listed
below the tree by path, and a selected member shows its tail. Run states are not recorded, so the
monitor shows none for a past run.

A session is `starting` until the channel connects, then `running`, `stopping` once the runtime
closed the channel for a requested shutdown, and `exited`; its view carries
the job, `connected`, the runtime's `control` facts (pid, `policy`, `measure_armed`), its `log_dir`,
`launch_ready` and `shutting_down`. Member records are the runtime's: `state` one of `pending`, `running`,
`stopped`, `respawning`, `failed`, `done`, `loading`, `loaded`, with a `detail` such as `pid` or
`exit_code`; the daemon adds `restarts`, the times the member was started again within the launch,
and `respawns`, the respawns among them. `policy` is `guard` (default: stop, restart, kill and shutdown are refused with
`refused` while the operation mode is autonomous or in transition) or `open`.

The Logs view lists composable nodes under their container, whose process holds
their output and whose exit they follow; a module or container shows the severest state among its
members. Failed members are listed with their error above the tree, in the order they failed, and
stay listed until the unit is launched again; one node failure shuts the whole runtime down, so
this list is what explains an exited launcher. A member whose output grows by 4 KB/s or more carries
a busy-log mark (a container's covers its composable nodes, whose output it holds), a folded entry
the mark of the busiest member within, and the selected member's head shows its rate. The legend at
the foot of the tree gives each mark's shape and colour.

### Live monitoring

The Explore activity overlays the live state of the launched members on its diagrams while a
daemon serves the page. The node diagram marks each launched node in its corner with the monitor's
run-state marks, a module or compute unit with the severest state among the nodes within; the mark
is the only change to the drawing, and its tooltip names the state and the restarts. The sequence
diagram treats the process gates of a member that is down as dead, so the
fold rules carry the outage along the chains: an or gate keeps to its live branches, an and gate
waiting on a dead one is dead itself. The side panel sums up the system's sessions, carries the
legend and switches the overlay off. States come from the same `member` events as the Launch
monitor, with a composable node following its container; a node never launched carries no mark.

The daemon polls the record root while an event stream is open (one directory listing per
`<package>/<system>/latency/<mode>`, every 2 s) and publishes `records.changed` once a new record
has held its size over two polls, whether the measuring runtime was launched by the daemon or from
a shell. Explore reloads the mode's latency file on that event, so a measure window's result
reaches the sequence diagram without a page reload; `health` reports the `record_root` watched.

`rviz.start` runs `rviz2` from the launch environment as a job of its own, one per daemon
(`conflict` while it runs), on the `--rviz-config` file, else on `autoware_launch`'s `autoware.rviz`
under the environment's ament prefixes, else on no configuration. The workbench offers it as
"Launch RViz" under the Launch activity's commands.

Launch jobs need the ROS environment the daemon itself does without: start `serve` from a sourced
shell, or pass `--env-script` with a setup script, sourced once at start into the job environment.
Without `ROS_DISTRO` in that environment `launch.start` and `rviz.start` answer `unavailable`. RViz
also needs the display of that environment (`DISPLAY` or `WAYLAND_DISPLAY`).
