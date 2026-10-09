"""Tests for error/warning detection and per-flight assignment (brief 15.4, #26-38)."""

from __future__ import annotations

from helpers.ulog_builder import UlogBuilder
from px4_flight_analyzer.error_analysis import analyze_file, analyze_segment, collect_log_messages
from px4_flight_analyzer.models import ErrorAnalysisStatus, ErrorPresence, FlightSegment, SegmentStatus, Severity
from px4_flight_analyzer.parser import load_ulog
from px4_flight_analyzer.time_resolution import format_relative_time

_BASE_US = 500_000


def _segment(path, start_us=2_000_000, end_us=22_000_000):
    return FlightSegment(0, path, start_us, end_us, SegmentStatus.COMPLETE)


def _relative_label_fn(segment):
    return lambda ts: format_relative_time(ts, segment.start_us)


def _land_only_ulog(tmp_path, log_message_adders):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(land, _BASE_US, landed=True)
    builder.add_sample(land, 2_000_000, landed=False)
    builder.add_sample(land, 22_000_000, landed=True)
    log_message_adders(builder)
    path = builder.write(tmp_path / "flight.ulg")
    return load_ulog(path), path


# --- #26: flight without any error messages -------------------------------------------------------


def test_flight_without_error_messages(tmp_path):
    ulog, path = _land_only_ulog(tmp_path, lambda b: None)
    messages, status, reasons = analyze_file(ulog)
    segment = _segment(path)
    result = analyze_segment(segment, messages, status, reasons, _relative_label_fn(segment))

    assert status is ErrorAnalysisStatus.VOLLSTAENDIG
    assert result.presence is ErrorPresence.NEIN
    assert result.details_text == ""


# --- #27: flight with one ERROR message -----------------------------------------------------------


def test_flight_with_one_error_message(tmp_path):
    ulog, path = _land_only_ulog(
        tmp_path, lambda b: b.add_log_message(5_000_000, "ERROR", "Estimator issue")
    )
    messages, status, reasons = analyze_file(ulog)
    segment = _segment(path)
    result = analyze_segment(segment, messages, status, reasons, _relative_label_fn(segment))

    assert result.presence is ErrorPresence.JA
    assert "ERROR [+00:00:03]: Estimator issue" in result.details_text


# --- #28: flight with one CRITICAL message ---------------------------------------------------------


def test_flight_with_one_critical_message(tmp_path):
    ulog, path = _land_only_ulog(
        tmp_path, lambda b: b.add_log_message(5_000_000, "CRITICAL", "GPS failure")
    )
    messages, status, reasons = analyze_file(ulog)
    segment = _segment(path)
    result = analyze_segment(segment, messages, status, reasons, _relative_label_fn(segment))

    assert result.presence is ErrorPresence.JA
    assert "CRITICAL [+00:00:03]: GPS failure" in result.details_text


# --- #29: flight with multiple error messages --------------------------------------------------------


def test_flight_with_multiple_distinct_error_messages(tmp_path):
    def add_messages(b):
        b.add_log_message(5_000_000, "ERROR", "Estimator issue")
        b.add_log_message(6_000_000, "CRITICAL", "GPS failure")

    ulog, path = _land_only_ulog(tmp_path, add_messages)
    messages, status, reasons = analyze_file(ulog)
    segment = _segment(path)
    result = analyze_segment(segment, messages, status, reasons, _relative_label_fn(segment))

    assert result.presence is ErrorPresence.JA
    assert result.matched_message_count == 2
    lines = result.details_text.split("\n")
    assert len(lines) == 2


# --- #30: flight with only WARNING messages ------------------------------------------------------


def test_flight_with_only_warning_messages_does_not_set_ja(tmp_path):
    ulog, path = _land_only_ulog(
        tmp_path, lambda b: b.add_log_message(5_000_000, "WARNING", "Low battery warning")
    )
    messages, status, reasons = analyze_file(ulog)
    segment = _segment(path)
    result = analyze_segment(segment, messages, status, reasons, _relative_label_fn(segment))

    assert result.presence is ErrorPresence.NEIN
    assert "WARNING [+00:00:03]: Low battery warning" in result.details_text


# --- #31: message before the flight start ----------------------------------------------------------


def test_message_before_flight_start_not_counted(tmp_path):
    ulog, path = _land_only_ulog(
        tmp_path, lambda b: b.add_log_message(1_000_000, "ERROR", "Pre-flight error, should not count")
    )
    messages, status, reasons = analyze_file(ulog)
    segment = _segment(path)
    result = analyze_segment(segment, messages, status, reasons, _relative_label_fn(segment))

    assert result.presence is ErrorPresence.NEIN
    assert result.matched_message_count == 0
    assert result.details_text == ""


# --- #32: message after landing ---------------------------------------------------------------------


def test_message_after_landing_not_counted(tmp_path):
    ulog, path = _land_only_ulog(
        tmp_path, lambda b: b.add_log_message(30_000_000, "ERROR", "Post-flight error, should not count")
    )
    messages, status, reasons = analyze_file(ulog)
    segment = _segment(path)
    result = analyze_segment(segment, messages, status, reasons, _relative_label_fn(segment))

    assert result.presence is ErrorPresence.NEIN
    assert result.matched_message_count == 0


# --- #33: multiple flights with different messages within one file ----------------------------------


def test_multiple_flights_with_different_messages(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(land, _BASE_US, landed=True)
    builder.add_sample(land, 2_000_000, landed=False)
    builder.add_log_message(5_000_000, "ERROR", "Error in flight 1")
    builder.add_sample(land, 12_000_000, landed=True)
    builder.add_sample(land, 20_000_000, landed=False)
    builder.add_log_message(25_000_000, "WARNING", "Warning in flight 2")
    builder.add_sample(land, 40_000_000, landed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    messages, status, reasons = analyze_file(ulog)
    segment_1 = FlightSegment(0, path, 2_000_000, 12_000_000, SegmentStatus.COMPLETE)
    segment_2 = FlightSegment(1, path, 20_000_000, 40_000_000, SegmentStatus.COMPLETE)

    result_1 = analyze_segment(segment_1, messages, status, reasons, _relative_label_fn(segment_1))
    result_2 = analyze_segment(segment_2, messages, status, reasons, _relative_label_fn(segment_2))

    assert result_1.presence is ErrorPresence.JA
    assert "Error in flight 1" in result_1.details_text
    assert "flight 2" not in result_1.details_text

    assert result_2.presence is ErrorPresence.NEIN
    assert "Warning in flight 2" in result_2.details_text
    assert "flight 1" not in result_2.details_text


# --- #34: missing timestamps on messages --------------------------------------------------------------


def test_missing_timestamps_flagged_as_incomplete(tmp_path):
    # A message with an anomalous zero timestamp cannot be reliably
    # assigned to any segment -- flag the whole file as 'Unvollständig'.
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(land, _BASE_US, landed=True)
    builder.add_sample(land, 2_000_000, landed=False)
    builder.add_sample(land, 22_000_000, landed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    # Directly construct a zero-timestamp message the way `collect_log_messages`
    # would surface one, since the real logging message type rejects
    # timestamp=0 at the file-format level the same way data messages do.
    from px4_flight_analyzer.models import LogMessage

    zero_ts_message = LogMessage(timestamp_us=0, severity=Severity.ERROR, text="Anomalous")
    from px4_flight_analyzer.error_analysis import file_level_integrity_flags

    reasons = file_level_integrity_flags(ulog, [zero_ts_message])
    assert any("zero timestamp" in reason for reason in reasons)


# --- #35: not reliably assignable messages (same as corruption-driven Unbekannt) ----------------------


def test_unreliable_assignment_due_to_corruption_yields_unbekannt(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(land, _BASE_US, landed=True)
    builder.add_truncated_sample(land, 1_000_000, landed=False)
    builder.add_sample(land, 2_000_000, landed=False)
    builder.add_log_message(5_000_000, "ERROR", "Estimator issue")
    builder.add_sample(land, 22_000_000, landed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)

    messages, status, reasons = analyze_file(ulog)
    assert status is ErrorAnalysisStatus.UNVOLLSTAENDIG

    segment = _segment(path)
    result = analyze_segment(segment, messages, status, reasons, _relative_label_fn(segment))
    assert result.presence is ErrorPresence.UNBEKANNT
    assert "[Note:" in result.details_text


# --- #36: incomplete error analysis (file_corruption flag) ---------------------------------------------


def test_incomplete_error_analysis_due_to_file_corruption(tmp_path):
    builder = UlogBuilder(start_timestamp_us=_BASE_US)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(land, _BASE_US, landed=True)
    builder.add_truncated_sample(land, 1_500_000, landed=False)
    builder.add_sample(land, 2_000_000, landed=False)
    builder.add_sample(land, 22_000_000, landed=True)
    path = builder.write(tmp_path / "flight.ulg")
    ulog = load_ulog(path)
    assert ulog.file_corruption is True

    messages, status, reasons = analyze_file(ulog)
    assert status is ErrorAnalysisStatus.UNVOLLSTAENDIG
    assert reasons


# --- #37: impossible error analysis ('Nicht möglich') -----------------------------------------------


def test_impossible_error_analysis_when_collection_raises(monkeypatch, tmp_path):
    ulog, path = _land_only_ulog(tmp_path, lambda b: None)

    # Simulate a catastrophic read failure on the tagged-message store:
    # `logged_messages_tagged.values()` on None raises AttributeError,
    # which `collect_log_messages` must turn into `MessageCollectionError`.
    monkeypatch.setattr(ulog, "_logged_messages_tagged", None)

    messages, status, reasons = analyze_file(ulog)
    assert status is ErrorAnalysisStatus.NICHT_MOEGLICH
    assert messages == []

    segment = _segment(path)
    result = analyze_segment(segment, messages, status, reasons, _relative_label_fn(segment))
    assert result.presence is ErrorPresence.UNBEKANNT
    assert "[Note:" in result.details_text


# --- #38: original messages preserved, special characters handled correctly ---------------------------


def test_original_message_text_preserved_verbatim(tmp_path):
    special_text = 'Special chars: ä ö ü ß "quoted" ; semicolon , comma'
    ulog, path = _land_only_ulog(tmp_path, lambda b: b.add_log_message(5_000_000, "ERROR", special_text))
    messages, status, reasons = analyze_file(ulog)
    assert messages[0].text == special_text

    segment = _segment(path)
    result = analyze_segment(segment, messages, status, reasons, _relative_label_fn(segment))
    assert special_text in result.details_text


def test_newline_in_message_text_preserved(tmp_path):
    # A literal newline inside one message's text must survive untouched;
    # CSV-level escaping is csv_export's job, not error_analysis's.
    multiline_text = "Line one\nLine two"
    ulog, path = _land_only_ulog(
        tmp_path, lambda b: b.add_log_message(5_000_000, "ERROR", multiline_text)
    )
    messages, _, _ = analyze_file(ulog)
    assert messages[0].text == multiline_text


# --- severity classification and consecutive-message collapsing --------------------------------------


def test_notice_info_debug_messages_are_dropped(tmp_path):
    def add_messages(b):
        b.add_log_message(3_000_000, "NOTICE", "routine notice")
        b.add_log_message(4_000_000, "INFO", "routine info")
        b.add_log_message(5_000_000, "DEBUG", "routine debug")

    ulog, _ = _land_only_ulog(tmp_path, add_messages)
    messages = collect_log_messages(ulog)
    assert messages == []


def test_emergency_and_alert_map_to_critical(tmp_path):
    def add_messages(b):
        b.add_log_message(3_000_000, "EMERGENCY", "emergency text")
        b.add_log_message(4_000_000, "ALERT", "alert text")

    ulog, _ = _land_only_ulog(tmp_path, add_messages)
    messages = collect_log_messages(ulog)
    assert all(m.severity is Severity.CRITICAL for m in messages)


def test_consecutive_identical_messages_collapsed_with_count(tmp_path):
    def add_messages(b):
        b.add_log_message(3_000_000, "ERROR", "Estimator issue")
        b.add_log_message(4_000_000, "ERROR", "Estimator issue")
        b.add_log_message(5_000_000, "ERROR", "Estimator issue")

    ulog, path = _land_only_ulog(tmp_path, add_messages)
    messages, status, reasons = analyze_file(ulog)
    segment = _segment(path)
    result = analyze_segment(segment, messages, status, reasons, _relative_label_fn(segment))

    assert result.matched_message_count == 3
    lines = result.details_text.split("\n")
    assert len(lines) == 1
    assert "×3" in lines[0]


def test_non_consecutive_identical_messages_not_collapsed(tmp_path):
    def add_messages(b):
        b.add_log_message(3_000_000, "ERROR", "Estimator issue")
        b.add_log_message(4_000_000, "WARNING", "Low battery warning")
        b.add_log_message(5_000_000, "ERROR", "Estimator issue")

    ulog, path = _land_only_ulog(tmp_path, add_messages)
    messages, status, reasons = analyze_file(ulog)
    segment = _segment(path)
    result = analyze_segment(segment, messages, status, reasons, _relative_label_fn(segment))

    lines = result.details_text.split("\n")
    assert len(lines) == 3


def test_tagged_log_messages_are_included(tmp_path):
    ulog, path = _land_only_ulog(
        tmp_path, lambda b: b.add_tagged_log_message(5_000_000, 3, "CRITICAL", "Tagged GPS failure")
    )
    messages, status, reasons = analyze_file(ulog)
    assert len(messages) == 1
    assert messages[0].tag == 3

    segment = _segment(path)
    result = analyze_segment(segment, messages, status, reasons, _relative_label_fn(segment))
    assert result.presence is ErrorPresence.JA
