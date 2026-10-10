"""PDF flight test summary rendering (reportlab Platypus).

Mirrors `csv_export.write_csv`'s shape: creates the output directory,
builds a flowable "story", and lets reportlab lay it out across pages.
One title page, then one entry per merged flight event group, then one
trailing page with space for handwritten notes.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import List
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .map_render import MAP_HEIGHT_PX, MAP_WIDTH_PX, assign_uav_colors, render_trajectory_map
from .models import FlightEventGroup, ReportHeader, UavTrajectory

logger = logging.getLogger(__name__)

_MAP_WIDTH_MM = 150
_MAP_HEIGHT_MM = _MAP_WIDTH_MM * MAP_HEIGHT_PX / MAP_WIDTH_PX
_NOTES_PAGE_LINE_COUNT = 22

_styles = getSampleStyleSheet()
_TITLE_STYLE = _styles["Title"]
_HEADING_STYLE = _styles["Heading1"]
_SUBHEADING_STYLE = _styles["Heading2"]
_NORMAL_STYLE = _styles["Normal"]
_MONO_STYLE = ParagraphStyle("MonoBody", parent=_NORMAL_STYLE, fontName="Courier", fontSize=8, leading=10)


def _esc(text: str) -> str:
    return escape(text or "")


def _text_block(text: str) -> Paragraph:
    body = escape(text) if text else "(none)"
    return Paragraph(body.replace("\n", "<br/>"), _MONO_STYLE)


def _format_time_or_blank(moment) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S") if moment is not None else "(unknown)"


def _format_battery(reading) -> str:
    return "n/a" if reading is None else f"{reading.percentage:.0f}%"


def _dedup_pilot_names(group: FlightEventGroup) -> List[str]:
    names: List[str] = []
    for member in group.members:
        name = member.flight_record.pilot_name
        if name not in names:
            names.append(name)
    return names


def _build_title_page(header: ReportHeader) -> List:
    label = header.header_label or "(project/date/place could not be determined from the input folder name)"
    return [
        Paragraph("PX4 Flight Test Summary", _TITLE_STYLE),
        Spacer(1, 10 * mm),
        Paragraph(_esc(label), _HEADING_STYLE),
        Spacer(1, 6 * mm),
        Paragraph(f"Number of flights: {header.flight_count}", _NORMAL_STYLE),
        Paragraph(f"Total flight hours: {header.total_flight_hours:.2f} h", _NORMAL_STYLE),
        PageBreak(),
    ]


def _build_legend(group: FlightEventGroup, uav_colors) -> Table:
    rows = []
    style_commands = [("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    for index, member in enumerate(group.members):
        uav_name = member.flight_record.uav_name
        color = uav_colors.get(uav_name, "#000000")
        rows.append(["", uav_name])
        style_commands.append(("BACKGROUND", (0, index), (0, index), colors.HexColor(color)))
    table = Table(rows, colWidths=[6 * mm, 60 * mm])
    table.setStyle(TableStyle(style_commands))
    return table


def _build_flight_entry(group: FlightEventGroup, uav_colors, map_fetch_online_tiles: bool) -> List:
    story: List = []
    uav_names_str = " / ".join(_esc(name) for name in group.uav_names)
    pilot_names_str = ", ".join(_esc(name) for name in _dedup_pilot_names(group))
    story.append(Paragraph(f"{uav_names_str} - Pilot(s): {pilot_names_str}", _HEADING_STYLE))

    trajectories = [
        UavTrajectory(
            uav_name=member.flight_record.uav_name,
            color=uav_colors.get(member.flight_record.uav_name, "#000000"),
            points=member.trajectory,
        )
        for member in group.members
    ]
    map_image = render_trajectory_map(trajectories, map_fetch_online_tiles)
    buffer = BytesIO()
    map_image.save(buffer, format="PNG")
    buffer.seek(0)
    story.append(RLImage(buffer, width=_MAP_WIDTH_MM * mm, height=_MAP_HEIGHT_MM * mm))
    story.append(Spacer(1, 2 * mm))
    story.append(_build_legend(group, uav_colors))
    story.append(Spacer(1, 4 * mm))

    for member in group.members:
        record = member.flight_record
        tr = record.time_resolution
        story.append(
            Paragraph(f"<b>{_esc(record.uav_name)}</b> -- Pilot: {_esc(record.pilot_name)}", _SUBHEADING_STYLE)
        )
        story.append(
            Paragraph(
                f"Start: {_format_time_or_blank(tr.resolved_start_utc)}   "
                f"Landing: {_format_time_or_blank(tr.resolved_end_utc)}",
                _NORMAL_STYLE,
            )
        )
        story.append(
            Paragraph(
                f"Battery: {_format_battery(member.battery_start)} -> {_format_battery(member.battery_end)}",
                _NORMAL_STYLE,
            )
        )
        story.append(Paragraph("Messages:", _NORMAL_STYLE))
        story.append(_text_block(record.error_analysis.details_text))
        story.append(Paragraph("State transitions:", _NORMAL_STYLE))
        story.append(_text_block("\n".join(member.transition_lines)))
        story.append(Spacer(1, 4 * mm))

    story.append(PageBreak())
    return story


def _build_notes_page() -> List:
    rows = [[""] for _ in range(_NOTES_PAGE_LINE_COUNT)]
    table = Table(rows, colWidths=[170 * mm], rowHeights=[9 * mm] * len(rows))
    table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.grey)]))
    return [Paragraph("Notes", _HEADING_STYLE), Spacer(1, 6 * mm), table]


def write_pdf_report(
    groups: List[FlightEventGroup],
    header: ReportHeader,
    output_path: Path,
    map_fetch_online_tiles: bool,
) -> None:
    """Write the PDF flight test summary to `output_path`.

    One title page, then one entry per flight event group (merging
    simultaneous multi-UAV flights), then one trailing notes page --
    always produced, even when `groups` is empty.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    all_uav_names: List[str] = []
    for group in groups:
        for name in group.uav_names:
            if name not in all_uav_names:
                all_uav_names.append(name)
    uav_colors = assign_uav_colors(all_uav_names)

    story: List = []
    story.extend(_build_title_page(header))
    for group in groups:
        story.extend(_build_flight_entry(group, uav_colors, map_fetch_online_tiles))
    story.extend(_build_notes_page())

    doc = SimpleDocTemplate(str(output_path), pagesize=A4)
    doc.build(story)

    logger.info("Wrote PDF flight test summary (%d flight event group(s)) to %s", len(groups), output_path)
