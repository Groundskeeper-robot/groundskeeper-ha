# groundskeeper-ha

A Home Assistant custom integration (HACS) for the Groundskeeper panel. It talks to the panel's HTTP API with an API
token, never to the robot. The panel repository's `AGENTS.md` has the project's safety rules; they apply here too.

- **Read-only for now.** No entity sends a command. Commands come later, through the panel's `POST /api/control/send`
  only, behind the panel's own switch and checks; never add a way around them.
- **No private data in Home Assistant.** Never store or log the robot's serial (use `robot_id()`), coordinates, the
  token or the panel address in entities, attributes, unique ids or diagnostics.
- **Tests are synthetic.** Fixtures come from the panel's demo mode (`DEMO=1`). Never test against a panel connected
  to a real robot, and never write a real mow's dates, times or readings into code, tests, docs or commits.
- The summary's shape is versioned (`api`). Read fields defensively; a missing one shows as unknown, never a crash.
- Run the tests on Linux, macOS, WSL or in a container (Home Assistant doesn't run on Windows).
