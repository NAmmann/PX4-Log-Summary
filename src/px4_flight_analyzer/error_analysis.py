"""Error/warning detection and per-flight assignment from `log_message` entries.

PX4's `log_message` (plain `ulog.logged_messages`) and
`logged_messages_tagged` carry a syslog-style severity byte alongside a
free-text message and an internal timestamp. `MessageLogging.log_level_str()`
in `pyulog.core` maps that byte to one of EMERGENCY/ALERT/CRITICAL/ERROR/
WARNING/NOTICE/INFO/DEBUG, or 'UNKNOWN' for an unrecognized byte -- this
module only ever uses that mapping, never a guessed or invented one.

Per the project brief, only three severities matter for the CSV
contract: WARNING, ERROR and CRITICAL. NOTICE/INFO/DEBUG are routine
status chatter, not findings, and are dropped entirely rather than
surfaced as low-priority warnings (that would risk burying real
warnings in noise). EMERGENCY and ALERT are both more severe than
CRITICAL in syslog terms, so both map onto CRITICAL -- the brief defines
no category above it.

Each file's messages are classified exactly once (`analyze_file`), since
message collection and the file-level completeness verdict
(`FehleranalyseStatus`) apply identically to every segment detected in
that file. Per-segment assignment (`analyze_segment`) then just filters
that one classified, sorted list to each segment's own time window --
a message outside `[start_us, end_us]` is never counted against a
flight it didn't happen during, even if it is the only message in the
whole file.
"""

from __future__ import annotations

import logging
from typing import Callable, List, Tuple

from .models import ErrorAnalysisResult, ErrorAnalysisStatus, ErrorPresence, FlightSegment, LogMessage, Severity

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    "EMERGENCY": Severity.CRITICAL,
    "ALERT": Severity.CRITICAL,
    "CRITICAL": Severity.CRITICAL,
    "ERROR": Severity.ERROR,
    "WARNING": Severity.WARNING,
    # NOTICE, INFO, DEBUG and any unmapped/'UNKNOWN' byte are intentionally
    # absent here -- they are dropped, not downgraded to WARNING.
}


class MessageCollectionError(Exception):
    """Raised when a file's log messages could not be read at all."""


def collect_log_messages(ulog) -> List[LogMessage]:
    """Collect and severity-classify every log_message entry in `ulog`.

    Merges `ulog.logged_messages` with every list in
    `ulog.logged_messages_tagged.values()` and returns the result sorted
    by timestamp. Messages whose severity is not in `SEVERITY_MAP`
    (NOTICE/INFO/DEBUG/UNKNOWN) are dropped, not included as any
    severity.

    :raises MessageCollectionError: if the underlying message lists on
        `ulog` cannot be read at all -- a genuinely exceptional
        condition, distinct from "the file simply has zero messages".
    """
    try:
        raw_entries = list(ulog.logged_messages)
        for tagged_list in ulog.logged_messages_tagged.values():
            raw_entries.extend(tagged_list)
    except (AttributeError, TypeError) as error:
        raise MessageCollectionError(f"could not read log messages: {error}") from error

    messages: List[LogMessage] = []
    for entry in raw_entries:
        level_str = entry.log_level_str()
        severity = SEVERITY_MAP.get(level_str)
        if severity is None:
            if level_str == "UNKNOWN":
                logger.debug("Dropping log_message with unrecognized severity byte %r", entry.log_level)
            continue
        messages.append(
            LogMessage(
                timestamp_us=int(entry.timestamp),
                severity=severity,
                text=entry.message,
                tag=getattr(entry, "tag", None),
            )
        )

    messages.sort(key=lambda message: message.timestamp_us)
    return messages


def file_level_integrity_flags(ulog, messages: List[LogMessage]) -> List[str]:
    """Return human-readable reasons the message data for this file is suspect.

    `[]` means nothing suspicious was found. Any non-empty result means
    the file-level status must be 'Unvollständig', not 'Vollständig',
    even though messages were successfully collected.
    """
    reasons = []
    if ulog.file_corruption:
        reasons.append("data corruption detected in this log file -- message list may be incomplete")
    if any(message.timestamp_us == 0 for message in messages):
        reasons.append("one or more messages have an anomalous zero timestamp")
    return reasons


def analyze_file(ulog) -> Tuple[List[LogMessage], ErrorAnalysisStatus, List[str]]:
    """Run the file-level error analysis exactly once per file.

    :return: `(classified_messages, status, integrity_reasons)`.
        `status` is 'Nicht möglich' if message collection itself failed,
        'Unvollständig' if collection succeeded but
        `file_level_integrity_flags` found a problem, otherwise
        'Vollständig' (even when `classified_messages` is empty --
        finding no errors is a valid, complete result).
    """
    try:
        messages = collect_log_messages(ulog)
    except MessageCollectionError as error:
        logger.error("Error analysis not possible: %s", error)
        return [], ErrorAnalysisStatus.NICHT_MOEGLICH, [str(error)]

    integrity_reasons = file_level_integrity_flags(ulog, messages)
    if integrity_reasons:
        for reason in integrity_reasons:
            logger.warning("Error analysis incomplete: %s", reason)
        return messages, ErrorAnalysisStatus.UNVOLLSTAENDIG, integrity_reasons

    return messages, ErrorAnalysisStatus.VOLLSTAENDIG, []


def _format_message_lines(messages: List[LogMessage], time_label_fn: Callable[[int], str]) -> List[str]:
    """Format classified messages into display lines, collapsing consecutive repeats.

    Only strictly *consecutive* identical `(severity, text)` entries are
    collapsed, preserving true chronological message order rather than
    grouping identical messages that are separated by a different one.
    """
    lines: List[str] = []
    index = 0
    total = len(messages)
    while index < total:
        current = messages[index]
        run_end = index + 1
        while (
            run_end < total
            and messages[run_end].severity == current.severity
            and messages[run_end].text == current.text
        ):
            run_end += 1
        run_length = run_end - index

        if run_length == 1:
            lines.append(f"{current.severity.value} [{time_label_fn(current.timestamp_us)}]: {current.text}")
        else:
            start_label = time_label_fn(current.timestamp_us)
            end_label = time_label_fn(messages[run_end - 1].timestamp_us)
            lines.append(
                f"{current.severity.value} [{start_label}–{end_label}, ×{run_length}]: {current.text}"
            )
        index = run_end
    return lines


def _format_details_text(
    in_window_messages: List[LogMessage],
    file_status: ErrorAnalysisStatus,
    integrity_reasons: List[str],
    time_label_fn: Callable[[int], str],
) -> str:
    lines: List[str] = []
    if file_status is not ErrorAnalysisStatus.VOLLSTAENDIG:
        note = "; ".join(integrity_reasons) if integrity_reasons else "error analysis incomplete"
        lines.append(f"[Note: {note}]")

    lines.extend(_format_message_lines(in_window_messages, time_label_fn))
    return "\n".join(lines)


def analyze_segment(
    segment: FlightSegment,
    file_messages: List[LogMessage],
    file_status: ErrorAnalysisStatus,
    integrity_reasons: List[str],
    time_label_fn: Callable[[int], str],
) -> ErrorAnalysisResult:
    """Assign a file's classified messages to one flight segment.

    :param file_messages: the full, file-level classified+sorted message
        list from `analyze_file` (shared across every segment of the
        same file).
    :param file_status: the file-level status from `analyze_file`.
    :param time_label_fn: formats one message's internal timestamp for
        display (absolute `HH:MM:SS` or relative `+HH:MM:SS`, depending
        on whether the file has a GPS-UTC time reference).
    """
    in_window_messages = [
        message for message in file_messages if segment.start_us <= message.timestamp_us <= segment.end_us
    ]

    if file_status is not ErrorAnalysisStatus.VOLLSTAENDIG:
        presence = ErrorPresence.UNBEKANNT
    else:
        has_error_or_critical = any(
            message.severity in (Severity.ERROR, Severity.CRITICAL) for message in in_window_messages
        )
        presence = ErrorPresence.JA if has_error_or_critical else ErrorPresence.NEIN

    details_text = _format_details_text(in_window_messages, file_status, integrity_reasons, time_label_fn)

    return ErrorAnalysisResult(
        status=file_status,
        presence=presence,
        details_text=details_text,
        matched_message_count=len(in_window_messages),
    )
