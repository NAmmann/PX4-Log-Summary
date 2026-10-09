#!/usr/bin/env python3
"""PX4 Flight Log Analyzer -- command-line entry point.

Run with `python px4_flight_analyzer.py` (Windows) or
`python3 px4_flight_analyzer.py` (Linux/macOS) -- no command-line
arguments, no configuration files. Every tunable setting lives in
`settings.py`, next to this script; see README.md for details.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import List

# The project has no build/packaging step (see README for why): make the
# `src/` package importable directly, anchored to this file's own
# location rather than the current working directory, so the tool
# behaves identically no matter where it is started from.
PROJECT_ROOT = Path(__file__).resolve().parent
_SRC_DIR = PROJECT_ROOT / "src"
# Insert PROJECT_ROOT first, then _SRC_DIR -- each insert(0, ...) pushes
# the previous entry back, so this ordering guarantees _SRC_DIR is
# searched *before* PROJECT_ROOT. That matters: PROJECT_ROOT contains
# this very file (px4_flight_analyzer.py), which would otherwise shadow
# the real `px4_flight_analyzer` package living under `src/`.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from px4_flight_analyzer.config import SettingsError, resolve_settings  # noqa: E402
from px4_flight_analyzer.csv_export import write_csv  # noqa: E402
from px4_flight_analyzer.error_analysis import analyze_file, analyze_segment  # noqa: E402
from px4_flight_analyzer.flight_detection import detect_flight_segments  # noqa: E402
from px4_flight_analyzer.gps import resolve_start_position  # noqa: E402
from px4_flight_analyzer.logging_setup import configure_logging  # noqa: E402
from px4_flight_analyzer.models import (  # noqa: E402
    ErrorAnalysisStatus,
    ErrorPresence,
    FlightRecord,
    ProcessingStats,
    SegmentStatus,
    TimeReferenceSource,
)
from px4_flight_analyzer.parser import ParseError, load_ulog  # noqa: E402
from px4_flight_analyzer.scanner import find_ulog_files, resolve_uav_pilot  # noqa: E402
from px4_flight_analyzer.time_resolution import (  # noqa: E402
    make_time_label_fn,
    resolve_file_time_reference,
    resolve_segment_time,
)

logger = logging.getLogger("px4_flight_analyzer")


def run(settings_module: ModuleType, project_root: Path) -> int:
    """Run the full scan -> detect -> analyze -> export pipeline.

    Separated from `main()` so tests can exercise the whole pipeline
    against an in-memory settings object pointed at a temporary
    directory, without needing to edit the real `settings.py` on disk.

    :return: process exit code (0 on success, 1 if settings.py itself
        is invalid -- the one case where the entire run is aborted
        before any file is touched).
    """
    initial_verbose = getattr(settings_module, "VERBOSE_LOGGING", False) is True
    configure_logging(initial_verbose)

    try:
        resolved = resolve_settings(settings_module, project_root)
    except SettingsError as error:
        logger.error("Invalid settings.py: %s", error)
        return 1

    # Settings are valid; (re-)apply the confirmed verbose flag in case
    # the raw attribute above was missing/malformed in a way that still
    # happened to evaluate falsy.
    configure_logging(resolved.verbose_logging)

    logger.info("PX4 Flight Log Analyzer starting")
    logger.info("Input directory: %s", resolved.input_directory)
    logger.info("Output CSV: %s", resolved.output_csv)
    logger.info("Timezone: %s", resolved.timezone_name)
    logger.info("Minimum flight duration: %.1f s", resolved.minimum_flight_duration_seconds)
    logger.info("Recursive search: %s", resolved.recursive_search)

    stats = ProcessingStats()

    ulog_files = find_ulog_files(resolved.input_directory, resolved.recursive_search)
    stats.files_found = len(ulog_files)
    logger.info("Found %d .ulg file(s)", stats.files_found)

    records: List[FlightRecord] = []

    for ulog_path in ulog_files:
        uav_pilot = resolve_uav_pilot(ulog_path, resolved.input_directory)
        if uav_pilot is None:
            stats.files_skipped_naming += 1
            logger.warning(
                "Skipping %s: no ancestor folder matches the UAV_PILOT naming scheme", ulog_path
            )
            continue
        uav_name, pilot_name = uav_pilot

        try:
            ulog = load_ulog(ulog_path)
        except ParseError as error:
            stats.files_parse_error += 1
            logger.error("Failed to parse %s: %s", ulog_path, error)
            continue
        stats.files_parsed_ok += 1

        segments = detect_flight_segments(ulog, ulog_path)
        stats.segments_detected += len(segments)
        if not segments:
            continue

        time_source, utc_offset_us = resolve_file_time_reference(ulog)
        file_messages, file_error_status, integrity_reasons = analyze_file(ulog)
        if file_error_status is not ErrorAnalysisStatus.VOLLSTAENDIG:
            stats.error_analyses_incomplete += 1

        for segment in segments:
            if segment.status is SegmentStatus.INCOMPLETE:
                stats.segments_excluded_incomplete += 1
                logger.info(
                    "%s: discarding segment #%d (incomplete): %s",
                    ulog_path,
                    segment.segment_index,
                    ", ".join(reason.value for reason in segment.incomplete_reasons),
                )
                continue

            if segment.duration_seconds < resolved.minimum_flight_duration_seconds:
                stats.segments_excluded_too_short += 1
                logger.info(
                    "%s: discarding segment #%d: duration %.1f s below minimum %.1f s",
                    ulog_path,
                    segment.segment_index,
                    segment.duration_seconds,
                    resolved.minimum_flight_duration_seconds,
                )
                continue

            gps_position = resolve_start_position(ulog, segment)
            time_resolution = resolve_segment_time(time_source, utc_offset_us, segment, resolved.timezone)
            time_label_fn = make_time_label_fn(
                time_source, utc_offset_us, segment.start_us, resolved.timezone
            )
            error_result = analyze_segment(
                segment, file_messages, file_error_status, integrity_reasons, time_label_fn
            )

            records.append(
                FlightRecord(
                    segment=segment,
                    uav_name=uav_name,
                    pilot_name=pilot_name,
                    gps_position=gps_position,
                    time_resolution=time_resolution,
                    error_analysis=error_result,
                )
            )

            stats.flights_exported += 1
            if error_result.presence is ErrorPresence.JA:
                stats.exported_ja += 1
            elif error_result.presence is ErrorPresence.NEIN:
                stats.exported_nein += 1
            else:
                stats.exported_unbekannt += 1
            if time_resolution.source is TimeReferenceSource.NICHT_ERMITTELBAR:
                stats.exported_without_time_reference += 1

    write_csv(records, resolved.output_csv, resolved.csv_delimiter, resolved.input_directory)
    stats.output_csv_path = resolved.output_csv

    _print_summary(stats)
    return 0


def _print_summary(stats: ProcessingStats) -> None:
    """Print the English end-of-run console summary (requirement: section 11)."""
    rows = [
        ("Log files found", stats.files_found),
        ("Log files parsed successfully", stats.files_parsed_ok),
        ("Log files with parse errors", stats.files_parse_error),
        ("Log files skipped (invalid UAV/Pilot naming)", stats.files_skipped_naming),
        ("Flight segments detected", stats.segments_detected),
        ("Segments discarded (shorter than minimum duration)", stats.segments_excluded_too_short),
        ("Segments discarded (incomplete start/end/armed data)", stats.segments_excluded_incomplete),
        ("Flights exported", stats.flights_exported),
        ("  Exported flights with Fehlermeldungen = Ja", stats.exported_ja),
        ("  Exported flights with Fehlermeldungen = Nein", stats.exported_nein),
        ("  Exported flights with Fehlermeldungen = Unbekannt", stats.exported_unbekannt),
        ("  Exported flights without a GPS-UTC time reference", stats.exported_without_time_reference),
        ("Files with an incomplete/impossible error analysis", stats.error_analyses_incomplete),
    ]

    print()
    print("=" * 70)
    print("PX4 Flight Log Analyzer -- processing summary")
    print("=" * 70)
    label_width = max(len(label) for label, _ in rows) + 2
    for label, value in rows:
        print(f"{label:<{label_width}} {value}")
    print(f"{'Output CSV file':<{label_width}} {stats.output_csv_path}")
    print("=" * 70)


def main() -> int:
    import settings  # the user-editable settings module, next to this script

    return run(settings, PROJECT_ROOT)


if __name__ == "__main__":
    sys.exit(main())
