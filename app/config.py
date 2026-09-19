"""Runtime configuration, overridable via environment variables."""

import os

# Default maximum number of occurrences returned per request.
DEFAULT_LIMIT = int(os.environ.get("OCCURRENCES_DEFAULT_LIMIT", "1000"))

# Hard upper bound for the per-request "limit" field.
MAX_LIMIT = int(os.environ.get("OCCURRENCES_MAX_LIMIT", "100000"))

# Hard cap on how many candidate instants the expander may scan while walking
# from dtstart towards the query window (protects against pathological rules
# such as FREQ=MINUTELY with a dtstart decades before the window).
SCAN_CAP = int(os.environ.get("OCCURRENCES_SCAN_CAP", "500000"))
