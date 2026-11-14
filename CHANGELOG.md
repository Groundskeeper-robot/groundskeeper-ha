# Changelog

## 0.1.0 — 2026-11-13

The first release.

### Added
- **The Groundskeeper card** (`custom:groundskeeper-card`), served by the integration and added to dashboards
  without a resource: status chips, the yard map with the run's track by RTK fix and the robot moving live (with a
  follow button), and the follow camera, preferring Frigate's own camera entity. A visual editor for its options. It
  reads only. New websocket command `groundskeeper/track` for the current run's track.
- The panel's live feed (`GET /api/ha/stream`, panel 0.20.1), with options for what it follows:
  a **Follow camera** sensor and camera entity (on), the yard map and live position for the card (on), and a
  **Position** device tracker (off: its position is recorded in history). The card's websocket commands
  (`groundskeeper/map`, `groundskeeper/subscribe`) and imagery views keep the API token on the Home Assistant server.
- A lawn mower entity, sensors and binary sensors from the panel's Home Assistant summary,
  set up from the UI with the panel's address and an API token, a new-token flow when the panel refuses the saved one,
  and redacted diagnostics. It sends nothing to the robot.
- Needs panel 0.20.0 or later. The **Robot link** binary sensor (was "Local link") is on when the robot answered
  the panel lately through the Yarbo cloud, as the panel's `local_link` field has meant since 0.20.0.
- **Commands**, off until switched on in the options: the mower entity's Start (resume when paused, else the plan
  the panel's start check names), Pause and Dock, and websocket commands for the card (`groundskeeper/plans`,
  `groundskeeper/preview`, `groundskeeper/command`). Each asks the panel's `POST /api/control/send`, which sends only
  with its Control switch on and every check passing; a refusal shows the panel's reason. Only start plan, pause,
  resume, stop and dock.
- **Controls on the card** (`show_controls`, with Commands on): Start (pick a plan, see the panel's checks, start
  only when it would), Pause, Stop and Dock at once, Resume on a second tap; which show follows the mower's state.
- The card shows a live preview in the dashboard's card picker, and the README has a screenshot of it.
- **Ground for a 3D view** (Map option): websocket `groundskeeper/ground` (the panel's ground: version, bounds,
  zooms, relief) and views for its Terrarium tiles and for the panel's MapLibre GL and three.js, which pass only with
  the contents of the pinned versions.
- **3D view on the card** (`view`: `2d`, `3d` or `3d_during_runs`, and a 3D / 2D button on the map): the yard on
  the panel's ground with the robot as a model that tilts with it, a 1× to 3× height choice (`ground_height`), and a
  follow camera that flies behind the robot. The libraries load the first time 3D shows; without WebGL or a ground
  the card keeps its flat map and says why.

### Changed
- README: one-click buttons to open the repository in the Marketplace (HACS before Home Assistant 2026.11) and
  to add the integration.
