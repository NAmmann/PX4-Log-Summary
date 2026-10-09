"""Shared data models used across all analyzer modules.

Keeping every cross-module data shape in one place avoids subtle
mismatches between, say, what `flight_detection` produces and what
`csv_export` expects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class Severity(Enum):
    """Normalized severity of a log message, independent of the raw PX4 log level byte."""

    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class SegmentStatus(Enum):
    """Whether a detected flight segment has reliably known start and end boundaries."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class IncompleteReason(Enum):
    """Why a flight segment could not be confirmed as COMPLETE."""

    MISSING_START_STATE = "missing_start_state"
    MISSING_END_STATE = "missing_end_state"
    IMPLAUSIBLE_DURATION = "implausible_duration"
    ARMED_SIGNAL_MISMATCH = "armed_signal_mismatch"


class ErrorAnalysisStatus(Enum):
    """Fixed German CSV strings for the 'FehleranalyseStatus' column. Do not translate."""

    VOLLSTAENDIG = "Vollständig"
    UNVOLLSTAENDIG = "Unvollständig"
    NICHT_MOEGLICH = "Nicht möglich"


class ErrorPresence(Enum):
    """Fixed German CSV strings for the 'Fehlermeldungen' column. Do not translate."""

    JA = "Ja"
    NEIN = "Nein"
    UNBEKANNT = "Unbekannt"


class TimeReferenceSource(Enum):
    """Fixed German CSV strings for the 'ZeitreferenzQuelle' column. Do not translate."""

    GPS_UTC = "GPS-UTC"
    NICHT_ERMITTELBAR = "Nicht ermittelbar"


@dataclass(frozen=True)
class GpsPosition:
    """A single selected GPS fix, chosen to represent a flight's start position."""

    latitude_deg: float
    longitude_deg: float
    source_topic: str
    sample_timestamp_us: int
    fix_gated: bool


@dataclass(frozen=True)
class LogMessage:
    """A single classified log_message entry from a ULog file."""

    timestamp_us: int
    severity: Severity
    text: str
    tag: Optional[int] = None


@dataclass
class FlightSegment:
    """One detected liftoff-to-touchdown window within a single ULog file."""

    segment_index: int
    source_file: Path
    start_us: int
    end_us: int
    status: SegmentStatus
    incomplete_reasons: list = field(default_factory=list)

    @property
    def duration_us(self) -> int:
        return self.end_us - self.start_us

    @property
    def duration_seconds(self) -> float:
        return self.duration_us / 1_000_000

    def unique_id(self, input_directory: Path) -> str:
        try:
            rel = self.source_file.relative_to(input_directory).as_posix()
        except ValueError:
            rel = self.source_file.as_posix()
        return f"{rel}#{self.segment_index}"


@dataclass
class ErrorAnalysisResult:
    """Outcome of the error/warning analysis for one flight segment."""

    status: ErrorAnalysisStatus
    presence: ErrorPresence
    details_text: str
    matched_message_count: int


@dataclass
class TimeResolution:
    """How (if at all) a file's internal boot-relative clock maps to real-world UTC."""

    source: TimeReferenceSource
    utc_offset_us: Optional[int]
    resolved_start_utc: Optional[datetime]
    resolved_end_utc: Optional[datetime]


@dataclass
class FlightRecord:
    """One fully analyzed, export-ready flight: the unit that becomes one CSV row."""

    segment: FlightSegment
    uav_name: str
    pilot_name: str
    gps_position: Optional[GpsPosition]
    time_resolution: TimeResolution
    error_analysis: ErrorAnalysisResult

    def sort_key(self, input_directory: Path):
        if self.time_resolution.source is TimeReferenceSource.GPS_UTC:
            return (0, self.time_resolution.resolved_start_utc, "", 0)
        try:
            rel_path = self.segment.source_file.relative_to(input_directory).as_posix().lower()
        except ValueError:
            rel_path = self.segment.source_file.as_posix().lower()
        return (1, None, rel_path, self.segment.segment_index)

    def to_csv_row(self) -> dict:
        tr = self.time_resolution
        if tr.source is TimeReferenceSource.GPS_UTC and tr.resolved_start_utc is not None:
            datum = tr.resolved_start_utc.strftime("%Y-%m-%d")
            start_zeit = tr.resolved_start_utc.strftime("%H:%M:%S")
            lande_zeit = (
                tr.resolved_end_utc.strftime("%H:%M:%S") if tr.resolved_end_utc is not None else ""
            )
        else:
            datum = ""
            start_zeit = ""
            lande_zeit = ""

        gps = self.gps_position
        start_lat = f"{gps.latitude_deg:.7f}" if gps is not None else ""
        start_lon = f"{gps.longitude_deg:.7f}" if gps is not None else ""

        return {
            "Datum": datum,
            "UAV": self.uav_name,
            "Pilot": self.pilot_name,
            "StartZeit": start_zeit,
            "LandeZeit": lande_zeit,
            "FlugDauerSekunden": f"{self.segment.duration_seconds:.1f}",
            "StartLatitude": start_lat,
            "StartLongitude": start_lon,
            "Fehlermeldungen": self.error_analysis.presence.value,
            "FehlermeldungenDetails": self.error_analysis.details_text,
            "FehleranalyseStatus": self.error_analysis.status.value,
            "ZeitreferenzQuelle": tr.source.value,
        }


@dataclass
class ProcessingStats:
    """Aggregate counters reported in the end-of-run console summary."""

    files_found: int = 0
    files_parsed_ok: int = 0
    files_parse_error: int = 0
    files_skipped_naming: int = 0
    segments_detected: int = 0
    segments_excluded_too_short: int = 0
    segments_excluded_incomplete: int = 0
    flights_exported: int = 0
    exported_ja: int = 0
    exported_nein: int = 0
    exported_unbekannt: int = 0
    error_analyses_incomplete: int = 0
    exported_without_time_reference: int = 0
    output_csv_path: Optional[Path] = None
