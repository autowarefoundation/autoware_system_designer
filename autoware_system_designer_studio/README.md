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

| Path                               | Role                                                                                  |
| ---------------------------------- | ------------------------------------------------------------------------------------- |
| `js/core/`                         | `SD` namespace, page events, theme, editor links, selection store, daemon client      |
| `js/diagrams/`                     | `DiagramBase`, the three diagram modules (registered on `SD.diagrams`)                |
| `js/inspector/`                    | info panel of the selected element                                                    |
| `js/shell/`                        | workbench frame: activity registry and rail, panel layout, status bar                 |
| `js/widgets/`                      | building blocks activities share: controls, main view frame, terminal, namespace tree |
| `js/activities/<id>/`              | one directory per rail activity: its parts, then `index.js` registering it            |
| `js/pages/`                        | page drivers                                                                          |
| `css/tokens.css`                   | colours (light/dark) and run-state tokens; every other stylesheet reads them          |
| `css/shell.css`, `css/widgets.css` | the frame and the shared widgets                                                      |
| `css/activities/<id>.css`          | the activity's own styles                                                             |

Each rail item of the workbench is an activity owning one view per slot, shown and hidden together:
its options in the side panel, its content in the main area and, when it selects elements, the
inspector. The top bar holds the system and the mode, which every activity reads; other selections
live in the activity that uses them, as store keys mirrored in the URL.

| Activity | Side panel                                         | Main area                                                                            |
| -------- | -------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Explore  | diagram list; entity search on searchable diagrams | diagram canvas; inspector shows the selected element                                 |
| Launch   | deploy, command method; unit launch (daemon)       | launch commands of the mode, or the state monitor (daemon `launch.status`; `?view=`) |
| Build    | target, compute unit, start; job list (daemon)     | log of the selected job (`?job=`)                                                    |

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
    --env-script install/setup.bash  # sourced once into the environment launch jobs run in
```

Open `http://127.0.0.1:8780/`. Pages follow the [site layout](#site-layout) and reflect the exports
as they are at each request, so a finished build shows on reload. The workbench Build activity
starts, follows and cancels builds. Nothing else of the install tree is served.

| Route              | Content                                                                                                      |
| ------------------ | ------------------------------------------------------------------------------------------------------------ |
| `GET /`            | Systems index                                                                                                |
| `GET /<site path>` | Page or data script of the [site layout](#site-layout)                                                       |
| `GET /assets/<f>`  | Web bundle shipped with this package (`css/`, `js/`)                                                         |
| `GET /api/health`  | Daemon status, including `workspace` and `restart_required`                                                  |
| `GET /api/systems` | Exported systems: modes, compute units with their status, build scripts per mode, deploy variants, page URLs |
| `GET /api/jobs`    | Jobs, newest first                                                                                           |
| `GET /api/launch`  | Launch sessions with their members                                                                           |
| `GET /api/events`  | Server-Sent Events with daemon-wide sequence numbers as event ids (topics below)                             |
| `POST /api/rpc`    | Request envelope `{"id", "method", "params"}` for any method below                                           |

| Method                                            | Params                                                                             | Result                                      |
| ------------------------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------- |
| `health`                                          | -                                                                                  | Daemon status                               |
| `systems.list`                                    | -                                                                                  | Exported systems                            |
| `jobs.list`                                       | -                                                                                  | `{"jobs": [job]}`                           |
| `jobs.get`                                        | `job`, `since` (log seq, default 0), `limit`                                       | `{"job", "log": {"first_seq", "lines"}}`    |
| `jobs.cancel`                                     | `job`                                                                              | job                                         |
| `build.start`                                     | `system` (`<pkg>:<name>`), `target`, `mode`, `compute_unit`                        | job                                         |
| `launch.start`                                    | `system`, `compute_unit`, `mode`, `deploy`, `args`, `policy`, `measure`, `respawn` | session                                     |
| `launch.status`                                   | `compute_unit` (optional)                                                          | `{"sessions": [session]}`, members included |
| `launch.stop`, `launch.restart`, `launch.kill`    | `compute_unit`, `pattern`                                                          | `{"members": [path]}` acted on              |
| `launch.shutdown`                                 | `compute_unit`                                                                     | `{"shutting_down": true}`                   |
| `measure.start`, `measure.stop`, `measure.status` | `compute_unit`                                                                     | `{"state", "message"}` of the window        |

| Event topic       | Data                                                                          |
| ----------------- | ----------------------------------------------------------------------------- |
| `hello`           | Sent first on each stream; no replay, so clients resynchronise by request     |
| `heartbeat`       | `{"time"}`                                                                    |
| `job`             | Job record on every state change                                              |
| `job.log`         | `{"job", "first_seq", "lines"}`, contiguous log lines                         |
| `systems.changed` | A build rebuilt an export package; re-read `systems.list`                     |
| `daemon`          | `{"restart_required": true}`                                                  |
| `launch`          | Session view (without members) on every change of a launch                    |
| `member`          | `{"compute_unit", "system", "path", "state", "detail", "time"}` of one member |
| `member.log`      | `{"compute_unit", "path", "stream", "lines"}`, new stdout/stderr lines        |

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
runtime's `member` and `member.log` events and relays `launch.stop|restart|kill` (a member `pattern`:
the exact path, else every path containing it), `launch.shutdown` and `measure.*` as requests. A
shutdown asked before the channel is up cancels the job. The runtime's own output is the job's log.

A session is `starting` until the channel connects, then `running`, `stopping` once the runtime
closed the channel for a requested shutdown, and `exited`; its view carries
the job, `connected`, the runtime's `control` facts (pid, `policy`, `measure_armed`), `launch_ready`
and `shutting_down`. Member records are the runtime's: `state` one of `pending`, `running`,
`stopped`, `respawning`, `failed`, `done`, `loading`, `loaded`, with a `detail` such as `pid` or
`exit_code`. `policy` is `guard` (default: stop, restart, kill and shutdown are refused with
`refused` while the operation mode is autonomous or in transition) or `open`.

The workbench's state monitor lists composable nodes under their container, whose process holds
their output and whose exit they follow; a module or container shows the severest state among its
members. The legend at the foot of the tree gives each mark's shape and colour.

Launch jobs need the ROS environment the daemon itself does without: start `serve` from a sourced
shell, or pass `--env-script` with a setup script, sourced once at start into the job environment.
Without `ROS_DISTRO` in that environment `launch.start` answers `unavailable`.
