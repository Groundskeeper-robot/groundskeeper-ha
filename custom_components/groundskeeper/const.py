"""Constants for the Groundskeeper integration."""

from datetime import timedelta
import logging

DOMAIN = "groundskeeper"
LOGGER = logging.getLogger(__package__)

# The shape of GET /api/ha/summary this integration understands. The panel raises it only for a breaking change.
SUMMARY_API = 1
SUMMARY_PATH = "/api/ha/summary"

UPDATE_INTERVAL = timedelta(seconds=30)
REQUEST_TIMEOUT_S = 15

# The panel's own limit for "Robot online": no robot data for this long and the robot counts as offline.
ROBOT_STALE_S = 120

# The live feed for the map card (GET /api/ha/stream, in the panel release after 0.20.0). Its `api` is versioned
# like the summary's.
LIVE_API = 1
LIVE_PATH = "/api/ha/stream"
MAP_PATH = "/api/map"
IMAGERY_PATH = "/api/imagery"
# The stream repeats its state every 5 s, so a read that waits this long means the connection is dead.
LIVE_READ_TIMEOUT_S = 30
# Entities fed by the live feed show unavailable once its last state event is older than this.
LIVE_STALE_S = 60
# Waits between reconnects: doubling from the first to the last; a panel without the stream waits the longest.
LIVE_RETRY_S = (2, 300)

# The Groundskeeper card, served by the integration and added to every dashboard's resources.
CARD_URL = "/groundskeeper_card/groundskeeper-card.js"

# Options (Settings → Devices & services → Groundskeeper → Configure).
CONF_MAP = "map"
CONF_FOLLOW_CAMERA = "follow_camera"
CONF_DEVICE_TRACKER = "device_tracker"
CONF_COMMANDS = "commands"
DEFAULT_OPTIONS = {CONF_MAP: True, CONF_FOLLOW_CAMERA: True, CONF_DEVICE_TRACKER: False, CONF_COMMANDS: False}

# The card's 3D view (Map option): the panel's ground (the robot's heights over the lawns and lidar around them) and
# the two libraries that draw it. Working the ground out can wait on a lidar download the first time, so it gets
# longer than other reads.
GROUND_PATH = "/api/elevation/ground"
GROUND_TIMEOUT_S = 90
# The libraries pass from the panel's /vendor/ only with these exact contents (the hashes the panel's Dockerfile
# checks), each served as its own fixed type: anything else the panel sends is refused, so a panel that was tampered
# with cannot run its own script on Home Assistant's address. A panel that moves to new versions needs an integration
# that knows their hashes; until then the card stays on the flat map.
VENDOR = {
    "maplibre-gl.js": ("text/javascript", "be9633c4d870e26fb37f1cfe5c5a77181667114003ea16207ac7850d8da8add1"),
    "maplibre-gl.css": ("text/css", "576b085fdd9487a65a19215328c1e086c07ce5bf6da09b666b3806d3d008dae9"),
    "three.module.min.js": ("text/javascript", "08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7"),
}
VENDOR_MAX_BYTES = 4_000_000

# Commands (the Commands option, off by default). They go only through the panel's POST /api/control/send, which
# sends them only with its Control switch on and every check passing, under the strict rules it keeps for an API token
# (unknown counts as a fail, no override). These five and nothing else: never the cameras or keep-awake, never manual
# drive. A preview asks the same checks without sending. The plan list is the panel's copy (never `fresh`, which
# would ask the robot).
COMMANDS = ("start_plan", "pause", "resume", "stop", "cmd_recharge")
PLANS_PATH = "/api/plans"
CONTROL_PREVIEW_PATH = "/api/control/preview"
CONTROL_SEND_PATH = "/api/control/send"

# Live updates for entities and websocket subscribers: async_dispatcher_send(hass, SIGNAL_LIVE.format(entry_id)).
SIGNAL_LIVE = f"{DOMAIN}_live_{{}}"
