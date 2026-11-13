# Changelog

## Unreleased

### Changed
- README: one-click buttons to open the repository in the Marketplace (HACS before Home Assistant 2026.11) and
  to add the integration.

### Added
- First version, read-only: a lawn mower entity, sensors and binary sensors from the panel's Home Assistant summary,
  set up from the UI with the panel's address and an API token, a new-token flow when the panel refuses the saved one,
  and redacted diagnostics. It sends nothing to the robot.
- Needs panel 0.20.0 or later. The **Robot link** binary sensor (was "Local link") is on when the robot answered
  the panel lately through the Yarbo cloud, as the panel's `local_link` field has meant since 0.20.0.
