# groundskeeper-ha

A Home Assistant custom integration (HACS) for the Groundskeeper panel. It talks to the panel's HTTP API with an API
token, never to the robot. The panel repository's `AGENTS.md` has the project's safety rules; they apply here too.

- **Commands only through the panel, and only when switched on.** The **Commands** option is off by default (decided
  by the maintainer, 2026-11-13). With it on, the mower entity and the card ask the panel's `POST /api/control/send`
  for one of five run commands (`COMMANDS`: start plan, pause, resume, stop, dock) with `confirm: true` and never an
  override; the panel's Control switch and checks decide. Never another command (cameras, keep-awake, manual drive),
  never a settings write, never a way around the panel's checks, and never `GET /api/plans?fresh=true` (it asks the
  robot). Tests use the demo's answers; never send a command to a real robot from a test.
- **No private data in Home Assistant.** Never store or log the robot's serial (use `robot_id()`), coordinates, the
  token or the panel address in entities, attributes, unique ids or diagnostics.
- **Tests are synthetic.** Fixtures come from the panel's demo mode (`DEMO=1`). Never test against a panel connected
  to a real robot, and never write a real mow's dates, times or readings into code, tests, docs or commits.
- The summary's shape is versioned (`api`). Read fields defensively; a missing one shows as unknown, never a crash.
- Run the tests on Linux, macOS, WSL or in a container (Home Assistant doesn't run on Windows).
