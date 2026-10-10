"""CSV row construction and file writing for the flight overview export.

All literal German header/enum strings live on `FlightRecord.to_csv_row`
(see `models.py`) -- this module only ever sorts and serializes already-
built row dictionaries, via the stdlib `csv` module, never by
constructing CSV text manually. That split keeps a future HTML/JSON/PDF
exporter free to reuse `build_rows()`'s output unchanged.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Dict, List

from .models import FlightRecord

logger = logging.getLogger(__name__)

CSV_COLUMNS = (
    "Datum",
    "UAV",
    "Pilot",
    "StartZeit",
    "LandeZeit",
    "StartZeitUTC",
    "LandeZeitUTC",
    "FlugDauerSekunden",
    "StartLatitude",
    "StartLongitude",
    "Fehlermeldungen",
    "FehlermeldungenDetails",
    "FehleranalyseStatus",
    "ZeitreferenzQuelle",
)


def build_rows(records: List[FlightRecord], input_directory: Path) -> List[Dict[str, str]]:
    """Sort flight records and convert each into a CSV-ready row dict.

    Sort order: flights with a resolved GPS-UTC time reference come
    first, in true chronological order by resolved start time; flights
    without any time reference follow, ordered by their source file's
    path relative to `input_directory` (case-insensitive, POSIX-
    normalized so the order doesn't depend on the host OS), then by
    segment index within that file.
    """
    sorted_records = sorted(records, key=lambda record: record.sort_key(input_directory))
    return [record.to_csv_row() for record in sorted_records]


def write_csv(
    records: List[FlightRecord], output_path: Path, delimiter: str, input_directory: Path
) -> None:
    """Write the final flights CSV to `output_path`.

    The header row is always written, even when `records` is empty.
    `output_path`'s parent directory is created automatically if it
    does not exist yet.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = build_rows(records, input_directory)

    with output_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)

    logger.info("Wrote %d flight record(s) to %s", len(rows), output_path)
