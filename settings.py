"""Central configuration for the PX4 Flight Log Analyzer.

Edit this file to change tool behavior. There are no command-line
arguments and no other configuration files -- everything that can be
tuned lives here.

Relative paths are always resolved against the project directory (the
directory containing this file), not the current working directory the
tool happens to be started from.
"""

from pathlib import Path

# Directory containing the flight logs to scan (searched recursively).
INPUT_DIRECTORY = Path("./flight_data")

# Destination file for the CSV export.
OUTPUT_CSV = Path("./output/flights.csv")

# Destination file for the PDF flight test summary.
OUTPUT_PDF = Path("./output/flight_test_summary.pdf")

# Minimum flight duration in seconds. Shorter flight segments are
# discarded and never written to the CSV.
MINIMUM_FLIGHT_DURATION_SECONDS = 10

# CSV field delimiter. Semicolon is the default so the file opens
# cleanly with German Excel locale settings.
CSV_DELIMITER = ";"

# IANA timezone name used for all date/time columns in the CSV output,
# e.g. "UTC" or "Europe/Berlin".
TIMEZONE = "UTC"

# Whether to search INPUT_DIRECTORY recursively (including all
# subfolders) or only its immediate contents.
RECURSIVE_SEARCH = True

# Enable additional diagnostic console output (flight detection, GPS
# selection, error analysis details).
VERBOSE_LOGGING = False

# Behavior when OUTPUT_CSV or OUTPUT_PDF already exists:
#   True  = overwrite the existing file
#   False = abort with a clear error message before processing anything
OVERWRITE_OUTPUT = True

# Whether the PDF report's trajectory maps fetch real OpenStreetMap
# tiles over the network. False skips straight to an offline blank-grid
# trajectory plot -- no network calls at all.
MAP_FETCH_ONLINE_TILES = True
