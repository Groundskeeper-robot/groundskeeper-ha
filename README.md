# Groundskeeper for Home Assistant

A Home Assistant integration, installed through [HACS](https://hacs.xyz), that shows your Yarbo robot in Home
Assistant through your [Groundskeeper](https://github.com/Groundskeeper-robot/groundskeeper) panel.

Home Assistant talks to the panel, never to the robot. The panel keeps the robot connection, the safety checks and the
audit log, and Home Assistant reads what the panel knows.

**This version only reads.** It sends nothing to the robot: the mower entity offers no Start, Pause or Dock. Commands
will come in a later version, behind a switch of their own in the panel and the same checks as the panel's buttons.

Independent community project, not affiliated with Yarbo.

## What you need

- A Groundskeeper panel, version **0.20.0 or later** (it has the Home Assistant summary, `GET /api/ha/summary`, and reaches the robot
  through the Yarbo cloud).
- Home Assistant 2026.3 or later, with HACS.
- Home Assistant must be able to reach the panel's address on your network.

## Install

[![Open this repository in the Home Assistant Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=Groundskeeper-robot&repository=groundskeeper-ha&category=integration)

1. **Download it.** The button above opens this repository in your Home Assistant: in the **Marketplace** from
   Home Assistant 2026.11, in **HACS** before that. Choose **Download**. Without the button: Marketplace (or HACS) →
   menu → **Custom repositories**, add `https://github.com/Groundskeeper-robot/groundskeeper-ha` with the type
   **Integration**, then download **Groundskeeper**.
2. Restart Home Assistant.
3. In the panel, go to **Settings → Access** and make an API token named "Home Assistant". It is shown once: copy it.
   An API token can read the panel and ask for robot commands; it can't change settings or switch control on.
4. **Add the integration.**

   [![Add the Groundskeeper integration to your Home Assistant.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=groundskeeper)

   Or **Settings → Devices & services → Add integration → Groundskeeper**. Enter the address you open the panel at
   (for example `http://192.0.2.20:8425`) and the token.

In Docker, use the panel's IP address: `.local` names often don't resolve inside a container.

## What it shows

One device, **Groundskeeper**, updated every 30 seconds.

| Entity | What it is |
|---|---|
| `lawn_mower.groundskeeper` | Mowing, paused, returning, docked, idle or error (an error code, or stuck), as the panel sees it. No actions in this version. |
| Battery, Positioning, Lawn, State | The battery, the RTK fix (`RTK fixed`, `RTK float`, `single point`, …), the lawn (`Dry`, `Damp`, `Wet`), and the panel's own state text |
| Last run, Next window | When the last run ended, with its result, and when the next schedule window opens, with its plan |
| Start check | `Ready` when a start of the next plan would go out now, else the panel's first reason why not. All reasons are in its attributes. |
| Run in progress, Robot online, Needs attention, Lost contact, Can start, Start waiting for confirmation | On/off. Needs attention is the panel's own stuck check; the mower then shows Error. |
| Diagnostic: Head, Robot link, Control, Waiting for RTK fix, Cloud needs sign-in | How the panel is connected and set up |
| Off by default: Satellites, Run distance, Cloud connection | Turn on what you want to chart |

The start check runs the panel's checks as a program, not a person: anything unknown counts as a fail. Nothing is
sent.

**Unavailable** means Home Assistant can't reach the panel, or the panel has had no robot data for 2 minutes. The
values that come from the robot (battery, positioning, the mower) then show unavailable rather than a stale reading.

The Groundskeeper icon ships inside the integration (`brand/`, Home Assistant 2026.3 and later), so it shows in
**Devices & services** and the **Add integration** list once the download is in place and Home Assistant has restarted.
The HACS store page before 2026.11 doesn't read icons shipped this way and says the icon isn't available; that's
HACS, not a missing file.

## Privacy

The robot's serial number and location never reach Home Assistant's database. The panel names the robot by a hash of
its serial, the summary carries no position, map or camera data, and **Download diagnostics** leaves out the token, the
panel's address and the serial.

## Development

Tests use [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component)
against summaries recorded from the panel's demo mode (`DEMO=1`, a made-up robot in a made-up yard). Home Assistant
doesn't run on Windows; use Linux, macOS, WSL or a container:

```
pip install -r requirements_test.txt
python -m pytest -q
```

Never point tests or a development Home Assistant at a panel connected to a real robot. The panel's own repository
has the safety rules: [AGENTS.md](https://github.com/Groundskeeper-robot/groundskeeper/blob/main/AGENTS.md).

[CodeRabbit](https://coderabbit.ai) reviews each pull request once it is ready for review, and again on each push
(`.coderabbit.yaml`; drafts wait). Its comments are suggestions: fix what is right, reply where it
is wrong, and the maintainer decides.

## License

MIT
