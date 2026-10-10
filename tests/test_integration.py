"""Full-pipeline integration tests (brief 15.5, #48-50, plus orchestration-level checks).

Loads `px4_flight_analyzer.py` via `importlib` under a distinct module
name (not `px4_flight_analyzer`, which is the real package under
`src/`) so the orchestrator's `run()` function can be exercised directly
against an in-memory settings object pointed at a temporary directory --
no need to edit the real `settings.py` on disk for every scenario.

One genuine subprocess test (`test_real_cli_invocation_without_arguments`)
additionally proves the actual `python px4_flight_analyzer.py` contract:
no CLI arguments, correct behavior when started from an unrelated CWD.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import shutil
import subprocess
import sys
import types
from pathlib import Path

from helpers.ulog_builder import UlogBuilder

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENTRY_SCRIPT = _PROJECT_ROOT / "px4_flight_analyzer.py"


def _load_entrypoint():
    spec = importlib.util.spec_from_file_location(
        "px4_flight_analyzer_entrypoint_under_test", _ENTRY_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_orchestrator = _load_entrypoint()


def _settings(**overrides):
    base = dict(
        INPUT_DIRECTORY=Path("flight_data"),
        OUTPUT_CSV=Path("output/flights.csv"),
        OUTPUT_PDF=Path("output/flight_test_summary.pdf"),
        MINIMUM_FLIGHT_DURATION_SECONDS=10,
        CSV_DELIMITER=";",
        TIMEZONE="UTC",
        RECURSIVE_SEARCH=True,
        VERBOSE_LOGGING=False,
        OVERWRITE_OUTPUT=True,
        # Belt-and-suspenders offline guarantee for the whole test suite,
        # independent of any map_render-level monkeypatching elsewhere.
        MAP_FETCH_ONLINE_TILES=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _epoch_us(year, month, day, hour=0, minute=0, second=0) -> int:
    moment = dt.datetime(year, month, day, hour, minute, second, tzinfo=dt.timezone.utc)
    return int(moment.timestamp() * 1_000_000)


def _build_normal_flight(path, anchor_us, error_message=True):
    builder = UlogBuilder(start_timestamp_us=500_000)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    armed = builder.add_topic("actuator_armed", [("uint64_t", "timestamp"), ("bool", "armed")])
    gps = builder.add_topic(
        "vehicle_gps_position",
        [
            ("uint64_t", "timestamp"),
            ("int32_t", "lat"),
            ("int32_t", "lon"),
            ("uint8_t", "fix_type"),
            ("uint64_t", "time_utc_usec"),
        ],
    )
    builder.add_sample(land, 500_000, landed=True)
    builder.add_sample(armed, 500_000, armed=False)
    builder.add_sample(gps, 500_000, lat=509375000, lon=69603000, fix_type=3, time_utc_usec=anchor_us)
    builder.add_sample(armed, 1_000_000, armed=True)
    builder.add_sample(land, 2_000_000, landed=False)
    if error_message:
        builder.add_log_message(5_000_000, "ERROR", "Estimator issue")
    builder.add_sample(land, 22_000_000, landed=True)
    builder.add_sample(armed, 23_000_000, armed=False)
    builder.write(path)


def _build_too_short_flight(path):
    builder = UlogBuilder(start_timestamp_us=500_000)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(land, 500_000, landed=True)
    builder.add_sample(land, 2_000_000, landed=False)
    builder.add_sample(land, 5_000_000, landed=True)
    builder.write(path)


def _extract_summary_value(output: str, label: str) -> int:
    for line in output.splitlines():
        if label in line:
            return int(line.strip().split()[-1])
    raise AssertionError(f"label {label!r} not found in output:\n{output}")


# --- #50: full end-to-end test from reading to CSV export ------------------------------------------


def test_full_end_to_end_pipeline(tmp_path):
    anchor_us = _epoch_us(2026, 6, 15, 10, 0, 0)
    _build_normal_flight(tmp_path / "flight_data" / "Falcon01_Max" / "flight.ulg", anchor_us)

    exit_code = _orchestrator.run(_settings(), tmp_path)
    assert exit_code == 0

    output_csv = tmp_path / "output" / "flights.csv"
    assert output_csv.exists()
    content = output_csv.read_text(encoding="utf-8")
    assert "Falcon01" in content
    assert "Max" in content
    assert "2026-06-15" in content
    assert ";Ja;" in content  # Fehlermeldungen = Ja, thanks to the ERROR message

    output_pdf = tmp_path / "output" / "flight_test_summary.pdf"
    assert output_pdf.exists()
    assert output_pdf.stat().st_size > 0


# --- #48: correct console summary -------------------------------------------------------------------


def test_console_summary_counters_are_consistent(tmp_path, capsys):
    anchor_us = _epoch_us(2026, 6, 15, 10, 0, 0)
    _build_normal_flight(
        tmp_path / "flight_data" / "Falcon01_Max" / "flight.ulg", anchor_us, error_message=False
    )
    _build_too_short_flight(tmp_path / "flight_data" / "Falcon01_Max" / "short.ulg")

    exit_code = _orchestrator.run(_settings(), tmp_path)
    assert exit_code == 0

    output = capsys.readouterr().out
    assert _extract_summary_value(output, "Log files found") == 2
    assert _extract_summary_value(output, "Log files parsed successfully") == 2
    assert _extract_summary_value(output, "Flight segments detected") == 2
    assert _extract_summary_value(output, "Segments discarded (shorter than minimum duration)") == 1
    assert _extract_summary_value(output, "Flights exported") == 1
    assert _extract_summary_value(output, "Fehlermeldungen = Nein") == 1
    assert _extract_summary_value(output, "Fehlermeldungen = Ja") == 0


def test_console_summary_reports_naming_and_parse_failures(tmp_path, capsys):
    # A file under a folder with no UAV_PILOT-shaped ancestor.
    builder = UlogBuilder(start_timestamp_us=500_000)
    land = builder.add_topic("vehicle_land_detected", [("uint64_t", "timestamp"), ("bool", "landed")])
    builder.add_sample(land, 500_000, landed=True)
    builder.write(tmp_path / "flight_data" / "NoUnderscore" / "flight.ulg")

    # An unparseable file under a valid UAV/Pilot folder.
    (tmp_path / "flight_data" / "Falcon01_Max").mkdir(parents=True)
    (tmp_path / "flight_data" / "Falcon01_Max" / "garbage.ulg").write_bytes(b"not a real ulog")

    exit_code = _orchestrator.run(_settings(), tmp_path)
    assert exit_code == 0

    output = capsys.readouterr().out
    assert _extract_summary_value(output, "Log files found") == 2
    assert _extract_summary_value(output, "skipped (invalid UAV/Pilot naming)") == 1
    assert _extract_summary_value(output, "Log files with parse errors") == 1
    assert _extract_summary_value(output, "Flights exported") == 0


# --- multi-UAV time overlap: 2 CSV rows merge into 1 PDF flight event group ------------------------


def test_multi_uav_overlapping_flights_merge_into_one_pdf_group(tmp_path, capsys):
    anchor_us_1 = _epoch_us(2026, 6, 15, 10, 0, 0)
    anchor_us_2 = _epoch_us(2026, 6, 15, 10, 0, 10)  # offset so the two flights' windows overlap
    _build_normal_flight(
        tmp_path / "flight_data" / "Falcon01_Max" / "flight.ulg", anchor_us_1, error_message=False
    )
    _build_normal_flight(
        tmp_path / "flight_data" / "Falcon02_Erika" / "flight.ulg", anchor_us_2, error_message=False
    )

    exit_code = _orchestrator.run(_settings(), tmp_path)
    assert exit_code == 0

    output = capsys.readouterr().out
    assert _extract_summary_value(output, "Flights exported") == 2
    assert _extract_summary_value(output, "PDF flight event groups") == 1

    output_pdf = tmp_path / "output" / "flight_test_summary.pdf"
    assert output_pdf.exists()
    assert output_pdf.stat().st_size > 0


# --- settings validation aborts the whole run before any scanning ----------------------------------


def test_invalid_settings_returns_exit_code_one(tmp_path):
    exit_code = _orchestrator.run(_settings(CSV_DELIMITER=""), tmp_path)
    assert exit_code == 1
    assert not (tmp_path / "output").exists()


def test_overwrite_output_false_aborts_before_scanning(tmp_path):
    (tmp_path / "flight_data").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "flights.csv").write_text("sentinel content")

    exit_code = _orchestrator.run(_settings(OVERWRITE_OUTPUT=False), tmp_path)
    assert exit_code == 1
    assert (tmp_path / "output" / "flights.csv").read_text() == "sentinel content"


# --- no duplicate CSV rows from reprocessing the same file ------------------------------------------


def test_no_duplicate_rows_from_the_same_file_processed_once(tmp_path):
    anchor_us = _epoch_us(2026, 6, 15, 10, 0, 0)
    _build_normal_flight(tmp_path / "flight_data" / "Falcon01_Max" / "flight.ulg", anchor_us)

    _orchestrator.run(_settings(), tmp_path)
    content = (tmp_path / "output" / "flights.csv").read_text(encoding="utf-8")
    assert content.count("Falcon01") == 1


# --- empty input directory produces header-only CSV, exit code 0 -----------------------------------


def test_empty_input_directory_produces_header_only_csv(tmp_path):
    (tmp_path / "flight_data").mkdir()
    exit_code = _orchestrator.run(_settings(), tmp_path)
    assert exit_code == 0

    lines = (tmp_path / "output" / "flights.csv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


# --- #49: start without command-line arguments, via a genuine subprocess, from an unrelated CWD ----


def test_real_cli_invocation_without_arguments(tmp_path):
    work_dir = tmp_path / "project_copy"
    work_dir.mkdir()
    shutil.copy(_ENTRY_SCRIPT, work_dir / "px4_flight_analyzer.py")
    shutil.copytree(_PROJECT_ROOT / "src", work_dir / "src")

    anchor_us = _epoch_us(2026, 6, 15, 10, 0, 0)
    _build_normal_flight(work_dir / "flight_data" / "Falcon01_Max" / "flight.ulg", anchor_us)

    (work_dir / "settings.py").write_text(
        "from pathlib import Path\n"
        'INPUT_DIRECTORY = Path("./flight_data")\n'
        'OUTPUT_CSV = Path("./output/flights.csv")\n'
        'OUTPUT_PDF = Path("./output/flight_test_summary.pdf")\n'
        "MINIMUM_FLIGHT_DURATION_SECONDS = 10\n"
        'CSV_DELIMITER = ";"\n'
        'TIMEZONE = "UTC"\n'
        "RECURSIVE_SEARCH = True\n"
        "VERBOSE_LOGGING = False\n"
        "OVERWRITE_OUTPUT = True\n"
        "MAP_FETCH_ONLINE_TILES = False\n"
    )

    unrelated_cwd = tmp_path / "somewhere_else_entirely"
    unrelated_cwd.mkdir()

    result = subprocess.run(
        [sys.executable, str(work_dir / "px4_flight_analyzer.py")],
        cwd=unrelated_cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "processing summary" in result.stdout.lower()
    assert (work_dir / "output" / "flights.csv").exists()
    assert (work_dir / "output" / "flight_test_summary.pdf").exists()
