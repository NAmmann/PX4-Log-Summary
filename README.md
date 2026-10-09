# PX4 Flight Log Analyzer

A command-line tool that scans a directory tree of PX4 `.ulg` flight logs and
produces a single CSV overview of every detected flight, including
technical error/warning findings. It runs on Windows and Linux, needs no
command-line arguments and no configuration files -- every tunable setting
lives in `settings.py`.

## Table of contents

- [Feature overview](#feature-overview)
- [Requirements](#requirements)
- [Installation (Windows)](#installation-windows)
- [Installation (Linux)](#installation-linux)
- [Running the tool](#running-the-tool)
- [Configuring `settings.py`](#configuring-settingspy)
- [Project structure](#project-structure)
- [UAV/Pilot folder-naming rules](#uavpilot-folder-naming-rules)
- [Flight detection strategy](#flight-detection-strategy)
- [Flight duration](#flight-duration)
- [GPS start-coordinate resolution](#gps-start-coordinate-resolution)
- [Error/warning detection and assignment](#errorwarning-detection-and-assignment)
- [CSV column reference](#csv-column-reference)
- [Timezones and time formats](#timezones-and-time-formats)
- [Behavior on faulty input](#behavior-on-faulty-input)
- [Running the tests](#running-the-tests)
- [Known limitations](#known-limitations)
- [Deliberate deviations from the literal brief](#deliberate-deviations-from-the-literal-brief)

## Feature overview

- Recursively scans a root folder for `.ulg` files (case-insensitive extension
  matching), tolerating unrelated files and nested subfolders of any depth.
- Derives UAV and pilot identity from folder names (`UAV_NAME_PILOT_NAME`).
- Detects actual liftoff/touchdown instants (not just recording start/end)
  from PX4 state-estimation topics, including multiple flights per file.
- Computes flight duration from the detected liftoff/touchdown pair.
- Resolves the GPS coordinate closest in time to each flight's liftoff.
- Discards flights shorter than a configurable minimum duration.
- Detects and classifies WARNING/ERROR/CRITICAL log messages and assigns
  them to the correct flight by timestamp.
- Exports one CSV row per valid flight, sorted chronologically.
- Prints an end-of-run console summary with consistent counters.
- Keeps processing the rest of the input when an individual file is
  corrupt, unreadable, or otherwise unusable.
- Never modifies or deletes the original log files.

## Requirements

- Python 3.11 or newer (see [Known limitations](#known-limitations) for the
  version this was actually developed and tested against).
- The packages listed in `requirements.txt` (`pyulog`, `tzdata`, `pytest`).

## Installation (Windows)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Python must be installed and reachable via the `python` command (check
with `python --version`). The official installer from python.org offers
an "Add python.exe to PATH" option during setup.

## Installation (Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running the tool

With the virtual environment active:

```bash
python px4_flight_analyzer.py      # Windows
python3 px4_flight_analyzer.py     # Linux
```

Or use the provided start scripts, which `cd` into the project directory
first so the tool behaves identically regardless of the current working
directory:

- **Windows**: double-click `run.bat`, or run it from a terminal. It keeps
  the console window open after the program ends so any error message
  can be read.
- **Linux**:
  ```bash
  chmod +x run.sh
  ./run.sh
  ```

No command-line arguments are needed or accepted. The tool reads every
setting from `settings.py`.

## Configuring `settings.py`

All tunable behavior lives in `settings.py`, next to `px4_flight_analyzer.py`:

| Setting | Meaning |
| --- | --- |
| `INPUT_DIRECTORY` | Root folder to scan for `.ulg` files. |
| `OUTPUT_CSV` | Destination path for the generated CSV. |
| `MINIMUM_FLIGHT_DURATION_SECONDS` | Flights shorter than this are discarded. |
| `CSV_DELIMITER` | CSV field separator (default `;`, for German Excel). |
| `TIMEZONE` | IANA timezone name used for all date/time output (e.g. `UTC`, `Europe/Berlin`). |
| `RECURSIVE_SEARCH` | Search all subfolders (`True`) or only the top level (`False`). |
| `VERBOSE_LOGGING` | Print additional flight-detection/GPS/error-analysis diagnostics. |
| `OVERWRITE_OUTPUT` | `True` overwrites an existing output file; `False` aborts the run with a clear error if the file already exists. |

`INPUT_DIRECTORY` and `OUTPUT_CSV` may be relative or absolute `Path`
objects. Relative paths are always resolved against the **project
directory** (the folder containing `px4_flight_analyzer.py`), determined
from the script's own file location -- never from the current working
directory the tool happens to be started from. This is verified by a test
that changes the working directory before invoking the tool.

Invalid settings (wrong type, out-of-range value, unresolvable timezone,
missing input directory, etc.) cause the tool to print one clear error
message naming the offending setting and abort before any file is
touched -- see [`config.py`](src/px4_flight_analyzer/config.py).

## Project structure

```text
px4-flight-analyzer/
├── px4_flight_analyzer.py        # entry point: path setup + orchestration
├── settings.py                   # all user-editable settings
├── requirements.txt
├── README.md
├── run.bat / run.sh               # start scripts for Windows / Linux
├── src/px4_flight_analyzer/
│   ├── __init__.py
│   ├── models.py                  # shared dataclasses/enums
│   ├── config.py                  # settings validation + path resolution
│   ├── logging_setup.py           # console logging configuration
│   ├── scanner.py                 # file discovery + UAV/Pilot assignment
│   ├── parser.py                  # pyulog.ULog loading chokepoint
│   ├── flight_detection.py        # liftoff/touchdown segment detection
│   ├── gps.py                     # start-coordinate resolution
│   ├── time_resolution.py         # GPS-UTC offset + time formatting
│   ├── error_analysis.py          # log-message classification + assignment
│   └── csv_export.py              # row building + CSV writing
├── tests/
│   ├── conftest.py
│   ├── helpers/ulog_builder.py    # synthetic .ulg file builder for tests
│   └── test_*.py                  # ~140 automated test cases
└── output/                        # created automatically if missing
```

There is deliberately no `pyproject.toml` or other packaging/build file
(see [Deliberate deviations](#deliberate-deviations-from-the-literal-brief)
below). `src/` is made importable via a plain `sys.path` insertion in
both `px4_flight_analyzer.py` and `tests/conftest.py`.

Each concern -- file discovery, UAV/Pilot assignment, ULog parsing, flight
detection, GPS resolution, error analysis, CSV export, console output --
lives in its own module, so a future HTML/PDF/JSON exporter or additional
filters (by UAV, pilot, time range) can be added without touching flight
detection or error analysis.

## UAV/Pilot folder-naming rules

UAV and pilot names are derived from a folder named `UAV_NAME_PILOT_NAME`
somewhere between a log file and the input root. The **first** underscore
separates the UAV name from the pilot name; any further underscores
belong to the pilot name:

| Folder | UAV | Pilot |
| --- | --- | --- |
| `Falcon01_Max` | `Falcon01` | `Max` |
| `Falcon03_Max_Mustermann` | `Falcon03` | `Max_Mustermann` |

Resolution rules (`scanner.resolve_uav_pilot`):

1. Starting from the log file's immediate parent folder, walk upward
   toward (but excluding) the input root.
2. The **nearest** ancestor folder whose name matches the
   `UAV_NAME_PILOT_NAME` scheme wins -- a log nested under a date or
   `archived/` subfolder is still correctly attributed to the UAV/Pilot
   folder further up.
3. A folder name without an underscore, or with an empty UAV or pilot
   part (e.g. `_Max`, `Falcon01_`), never matches and is skipped.
4. If no ancestor matches at all, the file is skipped entirely (logged as
   a warning, counted in the console summary under "Log files skipped");
   no UAV/Pilot is invented.

## Flight detection strategy

A ULog recording can start while already airborne and/or end before
touchdown, so the recording's own start/end timestamps are never used as
a flight's boundaries. Instead:

- **Primary signal**: `vehicle_land_detected.landed`. Every
  `False -> True` transition is a touchdown; every `True -> False`
  transition is a liftoff. `pyulog`'s own `list_value_changes` helper
  returns only genuine transitions (with timestamp 0 rows filtered out),
  which this project relies on directly rather than re-implementing
  change detection.
- If the first recorded `landed` value is already `False`, the real
  liftoff happened before recording started -- that segment's start is
  marked **missing** (reason `missing_start_state`), not guessed.
  Symmetrically, a recording that ends while still airborne gets its end
  marked **missing** (`missing_end_state`).
- **Cross-check**: each otherwise-plausible segment is additionally
  checked against the vehicle's armed state, preferring
  `actuator_armed.armed` and falling back to the legacy
  `vehicle_status.arming_state` enum (`== 2` meaning armed) on older
  logs. If *neither* topic was logged at all, this check is skipped
  entirely (no armed data cannot itself invalidate a segment). If a
  signal is present but the vehicle was never armed at or across the
  segment, it is marked incomplete (`armed_signal_mismatch`). The check
  is deliberately lenient (armed *anywhere* touching the window, not for
  the entire window) to tolerate normal timing skew between the land
  detector and the arming state machine.
- A segment whose duration is `<= 0` or longer than 24 hours is treated
  as implausible (`implausible_duration`) and marked incomplete.
- **Multiple flights per file** fall out naturally: every
  liftoff/touchdown pair becomes its own independently-evaluated segment
  with a stable identifier (`<relative file path>#<segment index>`).

Only the primary vehicle instance (`multi_id=0`) is considered for
`vehicle_land_detected`, `actuator_armed` and `vehicle_status` -- see
[Known limitations](#known-limitations).

If `vehicle_land_detected` is missing or has no usable data, no segments
can be detected in that file at all; this is logged clearly and the file
contributes zero flights (not a guessed single flight spanning the whole
recording).

## Flight duration

```text
duration = touchdown_timestamp - liftoff_timestamp
```

computed purely from the internal (boot-relative) microsecond timestamps
of the detected transitions -- never from the log file's own start/end.
`MINIMUM_FLIGHT_DURATION_SECONDS` (from `settings.py`) is applied once a
segment is confirmed `COMPLETE`:

- duration `< MINIMUM_FLIGHT_DURATION_SECONDS` -> discarded,
- duration `== MINIMUM_FLIGHT_DURATION_SECONDS` -> exported,
- duration `> MINIMUM_FLIGHT_DURATION_SECONDS` -> exported,
- a segment marked `INCOMPLETE` by flight detection is never exported,
  regardless of its (diagnostic-only) duration value.

## GPS start-coordinate resolution

PX4 logs position under different topic/field names depending on
firmware version: older firmware uses `vehicle_gps_position` with
integer `lat`/`lon` fields scaled by `1e7`; newer firmware uses
`sensor_gps` with floating-point `latitude_deg`/`longitude_deg` fields
already in degrees. Rather than branching on topic name, `gps.py`
inspects the actual field names and declared field *types* present in
each dataset and scales accordingly, so the same logic also covers
`vehicle_global_position` (typically `lat`/`lon` as `double`, no
`fix_type`), used only as a fallback.

Resolution order:

1. Search `vehicle_gps_position` and `sensor_gps`, requiring `fix_type >
   2` wherever a `fix_type` field exists.
2. Among all plausible candidates (not NaN, latitude within [-90, 90],
   longitude within [-180, 180], not the `(0, 0)` "never had a fix"
   sentinel), pick the one **closest in time** to the detected liftoff --
   never simply the first GPS sample in the file.
3. If the closest candidate is still more than 5 minutes away from
   liftoff, it is rejected as unrelated to this particular flight.
4. If no primary-topic fix qualifies, fall back to
   `vehicle_global_position` under the same closest-in-time and
   plausibility rules (without fix-type gating, since that topic
   typically has none).
5. If nothing qualifies, `StartLatitude`/`StartLongitude` are left blank
   and the reason is logged (missing data, no fix, implausible
   coordinates, or nothing within the time bound).

Latitude and longitude are always extracted by field name, never by
positional unpacking, so they cannot be swapped.

## Error/warning detection and assignment

- **Sources**: `ulog.logged_messages` (plain) and every list in
  `ulog.logged_messages_tagged.values()` (tagged), merged and sorted by
  timestamp.
- **Severity mapping** (from `pyulog`'s own `log_level_str()`):
  `EMERGENCY`/`ALERT`/`CRITICAL` -> `CRITICAL`; `ERROR` -> `ERROR`;
  `WARNING` -> `WARNING`. `NOTICE`/`INFO`/`DEBUG` and any unrecognized
  severity byte (`UNKNOWN`) are dropped entirely -- never shown, never
  counted, never downgraded into a warning.
- **Assignment**: a message counts toward a flight only if its timestamp
  falls within that flight's own `[liftoff, touchdown]` window
  (inclusive). A message before liftoff or after touchdown is never
  attributed to that flight, even if it is the only message in the file.
  With multiple flights per file, each flight's messages are assigned
  independently from the same classified, file-level message list.
- **File-level completeness** (`FehleranalyseStatus`, identical for every
  flight detected in that file):
  - `Nicht möglich` -- message collection itself failed (an exceptional
    condition, not "zero messages found").
  - `Unvollständig` -- collection succeeded, but `ulog.file_corruption` is
    set, or at least one collected message has an anomalous zero
    timestamp.
  - `Vollständig` -- otherwise, even when zero messages were found at
    all (finding no errors is a valid, complete result).
- **Per-flight verdict** (`Fehlermeldungen`):
  - `Unbekannt` whenever the file-level status is not `Vollständig` --
    *never* reported as error-free just because no message happened to
    fall in range.
  - Otherwise `Ja` if at least one `ERROR`/`CRITICAL` message falls
    within the flight's window, else `Nein`.
- **Details** (`FehlermeldungenDetails`): every matching message
  (including `WARNING`, which never drives `Fehlermeldungen` itself) is
  listed as `SEVERITY [time]: original text`. Strictly *consecutive*
  identical `(severity, text)` entries are collapsed into one line with
  an occurrence count and a time range
  (`SEVERITY [start–end, ×N]: text`) -- never reordered by content, and
  the original message text is never altered. If the file-level status
  is not `Vollständig`, an English bracketed note is prepended explaining
  why (corruption detected, anomalous timestamps, or collection failed),
  so the CSV is self-documenting without needing the console log.

## CSV column reference

Twelve columns, `;`-delimited by default, UTF-8, written via Python's
`csv` module (never hand-built strings), header always present even when
zero flights were found:

| # | Column | Description |
| --: | --- | --- |
| 1 | `Datum` | Flight date (`YYYY-MM-DD`), local to `TIMEZONE`. Blank if no time reference could be determined. |
| 2 | `UAV` | UAV name from the folder structure. |
| 3 | `Pilot` | Pilot name from the folder structure. |
| 4 | `StartZeit` | Liftoff time (`HH:MM:SS`), local to `TIMEZONE`. Blank if no time reference. |
| 5 | `LandeZeit` | Touchdown time (`HH:MM:SS`), local to `TIMEZONE`. Blank if no time reference. |
| 6 | `FlugDauerSekunden` | Flight duration in seconds (numeric, one decimal). |
| 7 | `StartLatitude` | Start latitude, decimal degrees with a decimal point. Blank if unavailable. |
| 8 | `StartLongitude` | Start longitude, decimal degrees with a decimal point. Blank if unavailable. |
| 9 | `Fehlermeldungen` | `Ja`, `Nein`, or `Unbekannt` (see above). |
| 10 | `FehlermeldungenDetails` | Formatted message list, or an explanatory note; may contain embedded newlines (correctly CSV-quoted). |
| 11 | `FehleranalyseStatus` | `Vollständig`, `Unvollständig`, or `Nicht möglich`. |
| 12 | `ZeitreferenzQuelle` | `GPS-UTC` if a GPS-UTC time anchor was found and applied, otherwise `Nicht ermittelbar`. |

See [Deliberate deviations](#deliberate-deviations-from-the-literal-brief)
for why there are twelve columns (and why `Datum`/`StartZeit`/`LandeZeit`
can be blank) rather than the brief's literal eleven.

Rows are sorted chronologically: all flights with a resolved `GPS-UTC`
time reference first, by true resolved start time; flights without any
time reference follow, ordered by their source file's path relative to
the input directory (case-insensitive, OS-independent), then by segment
index within that file.

## Timezones and time formats

- `TIMEZONE` in `settings.py` must be a valid IANA timezone name (`UTC`,
  `Europe/Berlin`, etc.), resolved via the standard library's `zoneinfo`.
  On a minimal Python install without a system timezone database,
  install the `tzdata` package (already listed in `requirements.txt`).
- A file's time reference is established once, from the earliest
  plausible `(internal_timestamp, time_utc_usec)` anchor found in
  `vehicle_gps_position`/`sensor_gps` (requiring an actual GPS fix where
  `fix_type` is available, and a UTC value between the year 2000 and five
  years from now). The resulting single offset converts *every*
  timestamp in that file, since the internal clock is one continuous
  monotonic counter for the whole boot session.
- Conversion always goes through true UTC first (`time_utc_usec` is a
  UTC epoch value), then into `TIMEZONE` -- so daylight-saving
  transitions and date changes across a timezone boundary are handled by
  the standard library, not by manual offset arithmetic.
- If no such anchor exists anywhere in a file, that file's flights are
  **still exported**, just with `Datum`/`StartZeit`/`LandeZeit` left
  blank and `ZeitreferenzQuelle = Nicht ermittelbar`; message timestamps
  in `FehlermeldungenDetails` fall back to elapsed time since that
  flight's own liftoff (`+HH:MM:SS`).
- In verbose mode (`VERBOSE_LOGGING = True`), the console log states the
  configured timezone at startup.

## Behavior on faulty input

- A corrupt, truncated, or unparseable `.ulg` file is caught, logged with
  its path and the reason, and counted under "Log files with parse
  errors" -- the rest of the input is still processed.
- A file whose folder structure doesn't resolve to a UAV/Pilot is
  skipped and counted separately ("Log files skipped").
- A file with internal data corruption (detected by `pyulog` itself) is
  still parsed; its error analysis is marked `Unvollständig` and every
  flight in that file gets `Fehlermeldungen = Unbekannt`, but flight
  detection and GPS resolution for that file are unaffected (they do not
  depend on the corrupted part).
- Missing GPS data, missing/inconsistent flight-state topics, and
  missing time references never raise -- they result in blank fields and
  a logged reason, never an invented value.
- An already-existing output file is either overwritten
  (`OVERWRITE_OUTPUT = True`) or causes a single, clear abort *before*
  any file is scanned (`OVERWRITE_OUTPUT = False`) -- this is the one
  case where the whole run stops rather than tolerating and continuing.
- The original `.ulg` files are only ever opened for reading.

## Running the tests

```bash
pip install -r requirements.txt   # includes pytest
python3 -m pytest tests/ -v
```

The suite (~140 cases across 8 files) covers every scenario from the
project brief: file discovery and UAV/Pilot assignment, flight detection
(including multi-flight files, missing start/end, implausible duration,
armed-state cross-checks), GPS resolution, error/warning detection and
assignment, CSV formatting and sorting, settings validation, and full
end-to-end runs -- including one genuine subprocess invocation of
`python px4_flight_analyzer.py` with no arguments from an unrelated
working directory.

All test fixtures are synthetic `.ulg` files built from scratch by
`tests/helpers/ulog_builder.py`, which writes the exact byte layout
`pyulog` expects (verified by reading `pyulog`'s own source), so the
fixtures are genuinely parsed by the real library, not mocked.

## Known limitations

- **Not validated against a real PX4 log.** No real-world log file was
  available while building this tool (offline environment). The GPS-UTC
  offset mechanism and every PX4 field-name/type assumption are based on
  reading `pyulog`'s source and documentation, not on a real log. It
  should be validated against actual flight data before production use.
- **Test runtime is Python 3.9.6, not 3.11+.** The tool targets Python
  3.11+ as required, and the code is written in 3.11+ style, but only
  Python 3.9.6 was available in the development/test environment.
  `from __future__ import annotations` is used throughout specifically
  so the code is valid on 3.9 while being written for 3.11+; no
  runtime-only 3.10+/3.11+ syntax is used. The test suite above was
  actually executed on 3.9.6; re-running it on 3.11+ is recommended but
  not expected to change behavior.
- **The structured `event` uORB topic is not decoded.** Newer PX4
  versions can log a binary `event` topic (decodable via
  `pyulog.px4_events.PX4Events`) in addition to `log_message`. This tool
  only analyzes `log_message`/tagged log messages. Reading
  `px4_events.py` directly showed that its offline-metadata validity
  check has a fallthrough path that can attempt a network download
  (`urllib.request.urlopen`) if the embedded metadata fails a cheap
  check -- to keep this tool strictly offline with zero network-call
  risk, `event`-topic decoding was left out of this version entirely.
  This is a real gap (some newer-firmware diagnostic information is not
  surfaced) and a candidate for a future version.
- **Single/primary vehicle instance only.** `vehicle_land_detected`,
  `actuator_armed`, `vehicle_status`, and GPS topics are all read at
  `multi_id=0`. No multi-vehicle or redundant-sensor-instance support.
- **No segment-merging heuristic.** A slow, multi-second landing bounce
  (`landed: True -> False -> True` shortly after touchdown) could in
  principle produce a spurious short extra "flight". This is usually
  filtered out by `MINIMUM_FLIGHT_DURATION_SECONDS` for fast bounces; no
  merge logic was added for slower ones, since merging risks silently
  collapsing two genuinely separate short missions into one.

## Deliberate deviations from the literal brief

A few choices made while implementing this tool extend or override the
literal wording of the original brief. Each is called out here
explicitly rather than silently:

1. **Language split.** All source code, identifiers, comments,
   docstrings, log/console output, and this README are in English. The
   **CSV output contract is German**, exactly as specified (column
   headers and the enum values `Ja`/`Nein`/`Unbekannt`,
   `Vollständig`/`Unvollständig`/`Nicht möglich`) -- preserving
   compatibility with German Excel users even though the codebase itself
   is English.
2. **Twelfth CSV column, `ZeitreferenzQuelle`.** The brief's literal spec
   has eleven columns and implies every valid flight gets a real date
   and time. In practice, a file can have no GPS-UTC anchor at all (no
   GPS topic logged, or a fix never acquired) -- the brief explicitly
   forbids inventing a start/landing time in that case. Rather than
   silently dropping those otherwise-valid flights from the CSV, they
   are **still exported**, with `Datum`/`StartZeit`/`LandeZeit` left
   blank and sorted after all time-resolved rows (by filename). The new
   `ZeitreferenzQuelle` column (`GPS-UTC` / `Nicht ermittelbar`) makes
   this basis visible directly in the CSV, rather than only in the
   console log.
3. **No `pyproject.toml`.** To avoid any ambiguity against the brief's
   "no configuration files" rule -- even though that rule's intent is
   about *runtime settings*, not build metadata -- `src/` is made
   importable via a plain `sys.path` insertion (in both
   `px4_flight_analyzer.py` and `tests/conftest.py`) instead of a
   packaging/build file.
4. **No `event`-topic decoding**, for the offline/no-network-risk reason
   explained under [Known limitations](#known-limitations).
