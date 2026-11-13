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

# The live feed for the map card (GET /api/ha/stream, in the panel release after 0.20.0). Its `api` is versioned like the summary's.
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

# Options (Settings → Devices & services → Groundskeeper → Configure).
CONF_MAP = "map"
CONF_FOLLOW_CAMERA = "follow_camera"
CONF_DEVICE_TRACKER = "device_tracker"
DEFAULT_OPTIONS = {CONF_MAP: True, CONF_FOLLOW_CAMERA: True, CONF_DEVICE_TRACKER: False}

# Live updates for entities and websocket subscribers: async_dispatcher_send(hass, SIGNAL_LIVE.format(entry_id)).
SIGNAL_LIVE = f"{DOMAIN}_live_{{}}"
