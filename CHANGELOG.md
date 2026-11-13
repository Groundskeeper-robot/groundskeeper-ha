# Changelog

## Unreleased

### Changed
- README: one-click buttons to open the repository in the Marketplace (HACS before Home Assistant 2026.11) and
  to add the integration.

### Added
- **The Groundskeeper card** (`custom:groundskeeper-card`), served by the integration and added to dashboards
  without a resource: status chips, the yard map with the run's track by RTK fix and the robot moving live (with a
  follow button), and the follow camera, preferring Frigate's own camera entity. A visual editor for its options. It
  reads only. New websocket command `groundskeeper/track` for the current run's track.
- The panel's live feed (`GET /api/ha/stream`, panel release after 0.20.0), with options for what it follows:
  a **Follow camera** sensor and camera entity (on), the yard map and live position for the card (on), and a
  **Position** device tracker (off: its position is recorded in history). The card's websocket commands
  (`groundskeeper/map`, `groundskeeper/subscribe`) and imagery views keep the API token on the Home Assistant server.
- First version, read-only: a lawn mower entity, sensors and binary sensors from the panel's Home Assistant summary,
  set up from the UI with the panel's address and an API token, a new-token flow when the panel refuses the saved one,
  and redacted diagnostics. It sends nothing to the robot.
