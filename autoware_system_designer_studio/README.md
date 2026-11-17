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

The studio reads exports, records and run logs by file contract through the runtime's `export_tree`,
`records`, `rates_record` and `run_log` modules; it imports nothing from the designer.

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
index.html                                         opens the system shown last, else the first one
assets/{css,js}/...                                web bundle
systems/<package>/<system>/index.html              workbench; ?activity=explore|launch|replay|build opens that activity
systems/<package>/<system>/config.js               workbench config
systems/<package>/<system>/icon.svg                the system's icon
systems/<package>/<system>/data/<mode>.js          system_structure/<mode>.json with vis guides attached
systems/<package>/<system>/data/<mode>_latency.js  newest latency record of the mode, else the exported reference
```

Links are relative, so `serve` and `pack` produce the same pages.

A system's icon is `<System>.icon.svg` beside its `<System>.system.yaml`; a system without one gets
a default tile with its monogram, coloured by its id.

## Web bundle

`web/` holds plain scripts (no build step, `file://` compatible) under the `SD` namespace and one
stylesheet per area; `pages/assets.py` declares the load order of each page.

| Path                               | Role                                                                                   |
| ---------------------------------- | -------------------------------------------------------------------------------------- |
| `js/core/`                         | `SD` namespace, page events, theme, editor links, selection store, daemon client       |
| `js/diagrams/`                     | `DiagramBase`, the three diagram modules (registered on `SD.diagrams`)                 |
| `js/inspector/`                    | info panel of the selected element                                                     |
| `js/shell/`                        | workbench frame: activity registry and rail, system menu, panel layout, status bar     |
| `js/widgets/`                      | building blocks activities share: controls, main view, terminal, namespace tree, marks |
| `js/activities/<id>/`              | one directory per rail activity: its parts, then `index.js` registering it             |
| `js/pages/`                        | page drivers                                                                           |
| `css/tokens.css`                   | colours (light/dark) and run-state tokens; every other stylesheet reads them           |
| `css/shell.css`, `css/widgets.css` | the frame and the shared widgets                                                       |
| `css/activities/<id>.css`          | the activity's own styles                                                              |

An activity of the workbench owns one view per slot, shown and hidden together: its options in the
side panel, its content in the main area and, when it selects elements, the inspector. The rail
lists the main views of every activity in captioned groups, Launch first. The top bar holds the system
selector, the mode, which every activity reads, and the launch bar; other selections
live in the activity that uses them, as store keys mirrored in the URL. The system selector lists
the systems of the site by package, each with its icon.

| Activity | Rail items                                        | Side panel                                     | Main area                                            |
| -------- | ------------------------------------------------- | ---------------------------------------------- | ---------------------------------------------------- |
| Explore  | Diagrams: Nodes, Logic, Sequence (`?diagram=`)    | entity search                                  | diagram canvas; inspector shows the selected element |
| Launch   | Launch: Control; Monitor: Logs, Topics (`?view=`) | none                                           | the view of the mode                                 |
| Replay   | Replay                                            | scope tree (`?scope=`)                         | boundary, bag match, configuration, run (daemon)     |
| Build    | Build                                             | target, compute unit, start; job list (daemon) | log of the selected job (`?job=`)                    |

The launch bar is the Launch activity's part of the top bar, shown on every activity: its drawer
toggle, with a daemon launch of every unit of the shown mode and deploy and shutdown of every
launched unit, and the launch state of the system with its mode, followed by that of each compute
unit. The state is of the mode launched, whatever mode is shown. The drawer holds the deploy, the launch options, the RViz start, and one row per
compute unit with the unit's controls. "Launch commands" in the drawer opens a popup with the
command of each unit, by launch method (designer runtime or XML launcher).

Launch has three main views. Control is the treemap of the
mode, every block coloured by its live member state and marked, before its name, with the state, a
busy log and, on a node, its restart count. Launch and Shutdown of each compute unit (and the
measurement window of a session whose runtime preloads the tracer) are in the unit's head bar;
"Measure from launch" opens the window at launch. A unit served by a peer daemon shows that daemon
beside them, by its SSH destination or else its address, coloured by the link state and marked when
the peer differs from this daemon; an `unreachable` unit offers no control until its daemon answers
again, except Forget, which drops the session that daemon last reported so the system can run another
mode. The status bar counts the connected peers and names the units of the others. Every declared unit has its own
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
to publish once), `intra` (intra-process only, which rcl does not count), `new`, `unseen` or
`offline` (every node of the topic on a unit that is not running). The list is one of three: Nodes,
the nodes in their namespace tree, each with the worst status of its outputs (else of its inputs);
Topics, the topic rows in the topic namespace tree; Table, the topic rows flat. Above the Nodes and
Topics lists a viewer draws the selected one left to right: a node with its ports between the
topics it subscribes and the topics it publishes, each with its status, rate, declared rate, type
and the node's own communication or callback time; a topic's detail between its publishers
and its subscribers, each with its own rate and time. Nodes are square blocks and topics
rounded tinted ones. A port of role `server` or `client` (else of a `/srv/` type, on an export
without roles) is a service: status `service`, no rate, since its calls are not counted. Services
sit in a Services group under the topic tree, below the topics on each side of a node as Client
and Server, dashed and of another tint, and a selected service is drawn between its server and its
clients. A topic card opens the topic in Topics, a node card the node in Nodes. The groups of a
tree start closed at the top and open below it, the Services group open with its sub-groups closed.
The view opens on the list of the selection with its row unfolded, and a port selected on an
Explore diagram selects its topic (the store's `topic`) in Topics. The view follows the rates of every connected session of the mode while shown, and
keeps the last rates of a launch that ended until its unit is launched again. A selector beside the
Topics title shows a past run of the mode instead, from the rates record in its log directory, over
a span brushed on a strip of the run's event density (counted events per second per 10 s sample
interval, scaled to the peak): the whole run at first, then any span between two samples. All three
select the store's `entity`, and a double click on a node of the map opens it in Logs.

An activity is one `SD.workbench.registerActivity(def)` call from its `index.js`, with its parts
attached to `SD.activities.<id>` by the scripts before it. `def` names the activity (`id`, `title`,
`icon`, `order`), its rail group (`group`) and main views (`views(config)`, each a rail item under
one store key), its daemon needs (`daemon`, `requires`), whether it owns side-panel options (`side`)
and an inspector view (`inspector`), what it adds to the status bar (`status(store)`), its part of
the top bar (`bar(slots, ctx)`, filled at startup), and `mount(slots, ctx)`, called on
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
    --peer sub_ecu=http://127.0.0.1:8782 \  # daemon serving another unit (repeatable), see Peers
    --peer-ssh sub_ecu=sub-ecu \   # SSH destination a peer on another device is forwarded from
    --site-file bench.site.yaml \  # peer bindings of a site (default: $AUTOWARE_SYSTEM_DESIGNER_SITE_FILE)
    --env-script install/setup.bash \  # sourced once into the environment launch jobs run in
    --rviz-config autoware.rviz    # opened by rviz.start (default: autoware_launch's autoware.rviz)
```

Open `http://127.0.0.1:8780/`. Pages follow the [site layout](#site-layout) and reflect the exports
as they are at each request, so a finished build shows on reload. The workbench Build activity
starts, follows and cancels builds. Nothing else of the install tree is served.

| Route              | Content                                                                                                      |
| ------------------ | ------------------------------------------------------------------------------------------------------------ |
| `GET /`            | Site entry, opening the system shown last                                                                    |
| `GET /<site path>` | Page or data script of the [site layout](#site-layout)                                                       |
| `GET /assets/<f>`  | Web bundle shipped with this package (`css/`, `js/`)                                                         |
| `GET /api/health`  | Daemon status, including `workspace`, `record_root`, `restart_required`, `heartbeat_s` and `peers`           |
| `GET /api/systems` | Exported systems: modes, compute units with their status, build scripts per mode, deploy variants, page URLs |
| `GET /api/jobs`    | Jobs, newest first                                                                                           |
| `GET /api/launch`  | Launch sessions with their members                                                                           |
| `GET /api/events`  | Server-Sent Events with daemon-wide sequence numbers as event ids (topics below)                             |
| `POST /api/rpc`    | Request envelope `{"id", "method", "params"}` for any method below                                           |

| Method                                            | Params                                                                                       | Result                                                  |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| `health`                                          | -                                                                                            | Daemon status                                           |
| `systems.list`                                    | -                                                                                            | Exported systems                                        |
| `jobs.list`                                       | -                                                                                            | `{"jobs": [job]}`                                       |
| `jobs.get`                                        | `job`, `since` (log seq, default 0), `limit`                                                 | `{"job", "log": {"first_seq", "lines"}}`                |
| `jobs.cancel`                                     | `job`                                                                                        | job                                                     |
| `build.start`                                     | `system` (`<pkg>:<name>`), `target`, `mode`, `compute_unit`                                  | job                                                     |
| `launch.start`                                    | `system`, `compute_unit`, `mode`, `deploy`, `args`, `policy`, `measure`, `respawn`, `launch` | session                                                 |
| `launch.status`                                   | `compute_unit` (optional)                                                                    | `{"sessions": [session]}`, members included             |
| `launch.stop`, `launch.restart`, `launch.kill`    | `compute_unit`, `pattern`, `exact` (optional: the member named `pattern` only)               | `{"members": [path]}` acted on                          |
| `launch.follow`                                   | `compute_unit`, `path`, `system` and `mode` (optional: the session of that mode)             | `{"path", "out": [lines], "err": [lines]}`              |
| `launch.unfollow`                                 | `compute_unit`, `path` (optional: every follow), `system` and `mode` (optional)              | `{"released": [path]}`                                  |
| `launch.runs`                                     | `system` (optional)                                                                          | `{"runs": [run]}`, newest first                         |
| `launch.run`                                      | `run`                                                                                        | run with `members: [{"path", "slug"}]`                  |
| `launch.run_tail`                                 | `run`, `member` (its directory name)                                                         | `{"run", "member", "out", "err"}`                       |
| `launch.run_rates`                                | `run`, `start`, `sample`, `span_s` (optional: the span between two sample numbers)           | run with `times`, `density` and the rates over the span |
| `launch.shutdown`                                 | `compute_unit`                                                                               | `{"shutting_down": true}`                               |
| `measure.start`, `measure.stop`, `measure.status` | `compute_unit`                                                                               | `{"state", "message"}` of the window                    |
| `monitor.follow`, `monitor.unfollow`              | `compute_unit`, `client` (optional: the caller's name)                                       | `{"interval_s"}`, `{"released"}`                        |
| `monitor.sample`                                  | `compute_unit`, `interval` (seconds, default 1, at most 20)                                  | rates over the interval                                 |
| `rviz.start`                                      | -                                                                                            | job                                                     |

| Event topic       | Data                                                                                                   |
| ----------------- | ------------------------------------------------------------------------------------------------------ |
| `hello`           | Sent first on each stream; no replay, so clients resynchronise by request                              |
| `heartbeat`       | `{"time"}`                                                                                             |
| `job`             | Job record on every state change                                                                       |
| `job.log`         | `{"job", "first_seq", "lines"}`, contiguous log lines                                                  |
| `systems.changed` | A build rebuilt an export package, or a peer changed; re-read `systems.list`                           |
| `daemon`          | `{"restart_required": true}`, or `{"peer": peer}` on a change of a peer                                |
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
open-ended; current values are `available`, `running` (an available unit with a launch active),
`remote_unsupported` (an onboard unit of another daemon that is not bound as a peer, or that the
bound peer does not serve), `unreachable` (the unit's peer does not answer), `offboard` and
`unsupported`.

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

### Replay

The Replay activity prepares a deterministic replay of the runtime (`autoware-system-designer-replay`,
see the runtime README) on any page and runs it where a daemon serves the page. Its side panel is the namespace tree of the mode with a
checkbox per entry: a checked entry selects every launched node at or under its path, and the
selection is kept as the fewest such paths (`?scope=`, comma separated), the runtime's `--scope`.
A node placed at a module's own path has an entry of its own below that path, named `<path>/<name>`.
Containers of the selected composable nodes join the scope.

The top of the main area keeps the scope diagram, at a height set by dragging the divider below it
(kept across visits). The diagram is drawn from the boundary the runtime computes, by topic:

- the **replay boundary** is a frame around the scope's nodes and containers; each node shows its
  topic ports, joined inside the frame where one node of the scope publishes what another reads;
- each **boundary input**, a topic a node outside the scope publishes, is a chip on the left side of
  the frame, joined to the ports that subscribe to it and coloured by its match against the bag;
- the **bag** block left of the frame lists the recorded topics that feed the scope, each joined to
  the input it feeds. A topic recorded under another name than its input is a remap, drawn dashed;
- an **output** port of a node is filled where its topic is recorded: a boundary output unless left
  unrecorded, a topic internal to the scope once chosen. The boundary outputs are not drawn.

A click on an input chip or its bag topic lists the sources of the input: the bag topics of its type
(choosing one recorded under another name remaps it onto the input), and for an input the bag lacks,
leaving it unfed or accepting it unfed. A click on an output port of a node toggles the
recording of its topic. The pointer on a port, chip or bag topic lights
the whole topic. The diagram follows every change of the scope, bag and choices: a view the viewer
zoomed or panned keeps the boundary frame in place, an untouched one is fitted again.

Below it, the **Bag** block takes the bag whose topics the boundary is matched against:

- a file dropped on the view or picked with "Open bag file": a `metadata.yaml`, or an `.mcap` file,
  whose topics, types and message counts are read in the page from its summary section. A dropped
  directory gives its `metadata.yaml`, else its `.mcap` file. A browser gives no path of a dropped
  file, so the path for the command is typed in the Bag field;
- a path or `file://` URI dropped as text, or typed: it fills the Bag field and, with a daemon, is
  read there ("Read bag"). A `.db3` bag without `metadata.yaml` is read this way.

Then comes the **Inputs and outputs** block, two check lists with the same choices as the diagram:

- **Inputs**: the boundary inputs with their match against the bag. An unchecked input is accepted
  unfed, without the remap onto it; an input the bag holds under its own name is always fed.
- **Outputs**: the boundary outputs, recorded unless unchecked (`skip_outputs`), then the topics
  internal to the scope, recorded once checked (`outputs`).

The checkbox at the head of a list clears every topic of it, or checks them all once none is, so
one click on the outputs' head replays without recording.

Then come the **services leaving the scope**, service ports whose other side is outside, each
accepted once checked.

Below them are the replay configuration these choices give (`scope`, `remap`, `accept`, `outputs`, `skip_outputs`),
to copy or download as `replay.yaml`, and the pre-flight and replay commands on the mode's structure
file with the bag path typed in the Bag field and the deploy of the top bar.

With a daemon, "Read bag" reads the topics of the bag at the typed path (`replay.bag`): a bag
without `metadata.yaml` gets one written beside its files by the rosbag2 reindexer, and is read from
its storage file where the directory is not writable. The Run
block starts the replay as a job (`replay.start`): "Pre-flight" checks the bag against the boundary
and stops, "Run replay" replays the scope, over the first seconds of the bag when a duration is
given and with the outputs kept as the reference when asked. The block shows the selected replay
job: its outcome (pre-flight passed or failed, outputs match or differ from the reference, no
reference yet, saved as the reference), the counts of the run, the pre-flight and endpoint problems,
the violations of the granted order, the topics that differ, the run directory and the job's log.

`replay.bag` takes `path` (a path or `file://` URI of the bag directory or one of its files) and
`generate` (write a missing `metadata.yaml`, off by default); its result names the `path`, the
`storage`, the time span, the `topics` and where they were read from as `metadata` (`file`,
`generated` or `storage`).

`replay.start` takes `system`, `mode`, `bag`, `deploy`, `scope` (design paths), `remap` (recorded
topic to fed topic), `accept`, `outputs`, `skip_outputs`, `check`, `save_reference`, `duration` and `domain_id`. The
daemon writes the configuration into a new run directory `<log base>/<stamp>_replay` and runs the
replay command of the launch environment on it; one replay runs per daemon (`conflict` while one
runs), and `jobs.cancel` ends it. The job's `detail` names the `run_dir` and, once the job ended,
carries the pre-flight `problems`, the `fed` and `recorded` topics, the `result` counts and the
`verdict` from the run's replay files. A replay shares its ROS domain with the units launched on the
daemon, which would answer the scope's requests and feed its topics: while one is launched, a
replay without a `domain_id` of its own answers `conflict` (a pre-flight starts no node and runs).

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

A compute unit runs one launch at a time, and the daemon one mode of one system at a time across its
units: a start on a running unit, or of another system or mode while a unit runs, answers `conflict`.
The daemon
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

Topic rates are followed the same way: `monitor.follow` makes its `client` a follower of a unit and
asks the runtime for its per-node rates (the tracer's counters, see the runtime README), which
arrive as `monitor` events every 2 s; `monitor.unfollow` ends that client's follow and the last one
releases them. Following twice under one name is following once, so a page follows again after its
event stream reconnected. Every follow ends with the last event stream, and a reconnected channel is
followed again.

The Logs view follows the selected node (a composable node through its container) while
it is shown, keeps the followed log once the launcher ended and marks where it ended.

A selector beside the Logs title shows a past launch instead of the live sessions. A run is
a directory under the runtime's log base (`/tmp/autoware_system_designer_logs`, and the parents of
the sessions' log directories) holding a `run.json`; a directory without one is not a run. The
daemon lists the runs of the system that no active session is writing, newest first, labelled by
start time, mode, deploy variant and compute unit from the manifest. A run is one runtime, so one
compute unit. A session and its
run name their launch (`launch`): a unit started while another unit of the system runs or starts the
mode joins that unit's launch, a peer's units as last reported, and the first unit opens a new one;
`launch.start` takes `launch` to name it. The Logs selector lists one entry per launch, labelled by
its first start, mode, deploy variant and compute units, and shows the members of all its runs on
one tree; the log files stay per run on their unit and are read from there. A member with a
directory in several runs of the launch, its unit started again within it, shows their tails in
start order, each under its run's label. A run naming no launch is an entry of its own. The Topics
selector lists the runs, grouped by launch. `launch.run` gives
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
waiting on a dead one is dead itself. States come from the same `member` events as the Launch
monitor, with a composable node following its container; a node never launched carries no mark.

The daemon polls the record root while an event stream is open (one directory listing per
`<package>/<system>/latency/<mode>`, every 2 s) and publishes `records.changed` once a new record
has held its size over two polls, whether the measuring runtime was launched by the daemon or from
a shell. Explore reloads the mode's latency file on that event, so a measure window's result
reaches the sequence diagram without a page reload; `health` reports the `record_root` watched.

`rviz.start` runs `rviz2` from the launch environment as a job of its own, one per daemon
(`conflict` while it runs), on the `--rviz-config` file, else on `autoware_launch`'s `autoware.rviz`
under the environment's ament prefixes, else on no configuration. The workbench offers it as
"Launch RViz" in the launch bar's drawer.

Launch jobs need the ROS environment the daemon itself does without: start `serve` from a sourced
shell, or pass `--env-script` with a setup script, sourced once at start into the job environment.
Without `ROS_DISTRO` in that environment `launch.start` and `rviz.start` answer `unavailable`. RViz
also needs the display of that environment (`DISPLAY` or `WAYLAND_DISPLAY`).

### Peers

One daemon runs per execution environment and serves the compute units in it. A daemon given the
others as peers serves their units as well, so a client talks to it alone:

```bash
autoware-system-designer-studio serve --port 8781 --compute-unit main_ecu \
    --peer sub_ecu_1=http://127.0.0.1:8782 --peer sub_ecu_2=http://127.0.0.1:8783
```

A binding names a compute unit and the `http://127.0.0.1:<port>` its daemon answers on; a peer checks
`Host` like any daemon, so the address is the peer's own loopback port or a forward that keeps the
port, and never the port of the daemon given the binding. Bindings also come from a site file,
which a `--peer` of the same unit replaces:

```yaml
peers:
  sub_ecu_1: http://127.0.0.1:8782
  sub_ecu_2: { url: "http://127.0.0.1:8783", ssh: sub-ecu-2 }
```

A peer on another device is reached through an SSH forward the daemon keeps itself. A binding with
an SSH destination (`ssh` in the site file, or `--peer-ssh <unit>=<destination>`) has a loopback
URL, and the daemon runs

```bash
ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=5 -o ServerAliveCountMax=3 \
    -L 127.0.0.1:8783:127.0.0.1:8783 -- sub-ecu-2
```

as a job of kind `tunnel`, one per peer address, started again 1 s to 15 s after it ends. Only the
running tunnel job stays in `jobs.list`; its log is the output of `ssh`. The destination is a host
of the SSH configuration of the daemon's user or `[user@]host`; user, port, keys and host key
checking come from that configuration, and nothing is ever prompted for. A forward that carries no
traffic for 15 s ends, so a frozen or disconnected device turns `unreachable` within that time.

A unit is either local (`--compute-unit`) or bound to a peer. The daemon keeps one link per peer
address: it reads the peer's health, systems, sessions and jobs, follows its event stream, and
reconnects with backoff from 0.5 s up to 10 s. A stream silent for three of the peer's heartbeat
intervals counts as lost.

| Where           | With peers                                                                                               |
| --------------- | -------------------------------------------------------------------------------------------------------- |
| `health`        | `peers`: `[{"units", "url", "state", "error", "version", "mismatch", "ssh", "tunnel"}]`                  |
| `systems.list`  | A peer's unit is `available`, `running` or `unreachable`, with the peer record under `daemon`            |
| Unit methods    | `launch.*`, `measure.*` and `monitor.*` naming a peer's unit are forwarded                               |
| Lists           | `launch.status`, `jobs.list` and `launch.runs` include the answering peers' sessions, launch jobs, runs  |
| Ids             | A peer's job and run ids read `<unit>/<id>`; `jobs.get`, `jobs.cancel` and `launch.run*` take them       |
| Events          | `launch`, `member`, `member.log`, `member.activity`, `monitor`, and `job`/`job.log` of launch jobs       |
| Follows         | Released on the peer once this daemon's last event stream closes; rate follows return after a reconnect  |
| Modes           | One mode of one system runs at a time across all daemons, by each peer's last reported sessions          |
| `launch.forget` | `{"compute_unit"}` of an unanswering peer: drops its last reported sessions, answers them as `forgotten` |
| `build.start`   | Refused for a peer's unit; a unit is built by its own daemon                                             |

Peer `state` is `connecting`, `connected` or `unreachable`. `mismatch` lists where a connected peer
differs from this daemon: version, method set, units it serves, and the modes, compute units and
deploy variants of the exported systems that have a unit on it. A mismatch is shown, not enforced.
While a peer is unreachable, requests for its units answer `unavailable`, `jobs.list` and
`launch.runs` leave it out, and `launch.status` lists its last reported sessions without members,
marked `stale`. Those sessions still hold their system to their mode until the peer answers again
or `launch.forget` drops them, which publishes each as a `launch` event marked `forgotten`; the
unit itself may still be running. The mode rule holds for launches made through the daemon that
has the others as peers: a peer knows only its own units, so a client of a peer alone is not held
to it. `ssh` is the peer's SSH destination and `tunnel` the id of its current tunnel job, both null
for a peer reached directly.

### Multi-unit environment

[`demo/docker_multi_unit`](../demo/docker_multi_unit/README.md) runs the daemons of the demo system
`TypeAlpha` in Docker containers, as containers of one ECU, as separate ECUs reached over SSH, or
as a mix of both, one daemon peered with the others in each, with tests that drive them through its
API and cut devices off.
