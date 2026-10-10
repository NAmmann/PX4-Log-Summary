"""Title-page metadata parsing and summary-statistic computation for the PDF report.

The header format is `YYYY/MMDD_<PROJECT>_<PLACE>`: `INPUT_DIRECTORY`
itself is the `MMDD_<PROJECT>_<PLACE>` folder, and its *parent*
folder's name is the `YYYY` year -- settled with the user, not an
inference from file contents.

Splitting `MMDD_<PROJECT>_<PLACE>` reuses `scanner.parse_uav_pilot_folder_name`
directly: that function's "first separator significant, rest belongs
to the last part" rule is a generic two-part folder-name split, not
actually specific to UAV/Pilot semantics, so it is applied twice here
(once to split MMDD from the rest, once more to split PROJECT from
PLACE) rather than reimplemented.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from .models import FlightEventGroup, FlightRecord, ReportHeader
from .scanner import parse_uav_pilot_folder_name

logger = logging.getLogger(__name__)


def parse_report_header_fields(input_directory: Path) -> Optional[Tuple[str, str, str, str]]:
    """Parse `(year, mmdd, project, place)` from `input_directory`'s own path.

    :return: `None` if `input_directory`'s parent is not a 4-digit year
        folder, or `input_directory.name` does not split cleanly into
        `MMDD_PROJECT_PLACE` -- never invented, only ever parsed.
    """
    resolved = input_directory.resolve()
    year = resolved.parent.name
    if not (len(year) == 4 and year.isdigit()):
        logger.warning("Cannot parse report header: parent folder '%s' is not a 4-digit year", year)
        return None

    folder_name = resolved.name
    first_split = parse_uav_pilot_folder_name(folder_name)
    if first_split is None:
        logger.warning(
            "Cannot parse report header: folder name '%s' does not match 'MMDD_PROJECT_PLACE'",
            folder_name,
        )
        return None
    mmdd, project_place = first_split

    if not (len(mmdd) == 4 and mmdd.isdigit()):
        logger.warning("Cannot parse report header: '%s' is not a 4-digit MMDD value", mmdd)
        return None

    second_split = parse_uav_pilot_folder_name(project_place)
    if second_split is None:
        logger.warning(
            "Cannot parse report header: '%s' does not split into PROJECT_PLACE", project_place
        )
        return None
    project, place = second_split

    return year, mmdd, project, place


def compute_flight_hours(records: List[FlightRecord]) -> float:
    """Sum every exported flight's own duration, in hours.

    Deliberately *not* deduplicated across overlapping multi-UAV
    flights -- each UAV's own flight time counts, even when several
    UAVs were airborne at once and therefore share one merged
    `FlightEventGroup` entry on the printed flight list.
    """
    return sum(record.segment.duration_seconds for record in records) / 3600


def build_report_header(
    input_directory: Path, groups: List[FlightEventGroup], records: List[FlightRecord]
) -> ReportHeader:
    """Build the PDF title page's header from parsed folder names and flight stats."""
    parsed = parse_report_header_fields(input_directory)
    if parsed is None:
        year = mmdd = project = place = None
        header_label = ""
    else:
        year, mmdd, project, place = parsed
        header_label = f"{year}/{mmdd}_{project}_{place}"

    return ReportHeader(
        year=year,
        mmdd=mmdd,
        project=project,
        place=place,
        header_label=header_label,
        flight_count=len(groups),
        total_flight_hours=compute_flight_hours(records),
    )
