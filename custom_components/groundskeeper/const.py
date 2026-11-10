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
