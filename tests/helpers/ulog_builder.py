"""From-scratch binary `.ulg` file builder for tests.

Writes files byte-for-byte according to the ULog format as implemented
by `pyulog.core.ULog` (verified by reading that module's source directly
rather than relying on pyulog's own, private, write-back code path). This
keeps the test fixtures independent of pyulog internals that aren't part
of its public read API, while still producing files the installed
`pyulog` version can actually parse.

Message layout reference (little-endian throughout):
  file header   : 7 magic bytes + 1 version byte + 8 byte uint64 start timestamp
  message header: uint16 payload size + uint8 message type
  FORMAT  ('F')  : b"<name>:<type> <field>;<type> <field>;..."
  ADD_LOGGED_MSG ('A'): uint8 multi_id + uint16 msg_id + name bytes
  DATA    ('D')  : uint16 msg_id + packed field bytes (declared field order)
  LOGGING ('L')  : uint8 log_level + uint64 timestamp + message bytes
  LOGGING_TAGGED ('C'): uint8 log_level + uint16 tag + uint64 timestamp + message bytes
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

HEADER_BYTES = b"\x55\x4c\x6f\x67\x01\x12\x35"
FILE_VERSION = 1

MSG_TYPE_FORMAT = ord("F")
MSG_TYPE_DATA = ord("D")
MSG_TYPE_ADD_LOGGED_MSG = ord("A")
MSG_TYPE_LOGGING = ord("L")
MSG_TYPE_LOGGING_TAGGED = ord("C")

_TYPE_CODES = {
    "int8_t": ("b", 1),
    "uint8_t": ("B", 1),
    "int16_t": ("h", 2),
    "uint16_t": ("H", 2),
    "int32_t": ("i", 4),
    "uint32_t": ("I", 4),
    "int64_t": ("q", 8),
    "uint64_t": ("Q", 8),
    "float": ("f", 4),
    "double": ("d", 8),
    "bool": ("?", 1),
}

LOG_LEVELS = {
    "EMERGENCY": ord("0"),
    "ALERT": ord("1"),
    "CRITICAL": ord("2"),
    "ERROR": ord("3"),
    "WARNING": ord("4"),
    "NOTICE": ord("5"),
    "INFO": ord("6"),
    "DEBUG": ord("7"),
}


def _message_header(payload: bytes, msg_type: int) -> bytes:
    return struct.pack("<HB", len(payload), msg_type) + payload


@dataclass
class TopicHandle:
    """Handle returned by `UlogBuilder.add_topic`, used to add samples for that topic."""

    name: str
    multi_id: int
    msg_id: int
    field_names: List[str] = field(default_factory=list)
    field_types: List[str] = field(default_factory=list)

    @property
    def struct_format(self) -> str:
        return "<" + "".join(_TYPE_CODES[t][0] for t in self.field_types)

    @property
    def item_size(self) -> int:
        return sum(_TYPE_CODES[t][1] for t in self.field_types)


class UlogBuilder:
    """Incrementally builds an in-memory ULog file, then writes it to disk."""

    def __init__(self, start_timestamp_us: int = 0):
        self.start_timestamp_us = start_timestamp_us
        self._format_defs: "Dict[str, List[Tuple[str, str]]]" = {}
        self._topics: Dict[Tuple[str, int], TopicHandle] = {}
        self._next_msg_id = 0
        # list of (timestamp_us, raw_message_bytes); assembled into the
        # data section in timestamp order.
        self._data_section_messages: List[Tuple[int, bytes]] = []

    def add_topic(self, name: str, fields: List[Tuple[str, str]], multi_id: int = 0) -> TopicHandle:
        """Declare a topic/dataset.

        :param name: dataset name, e.g. "vehicle_land_detected".
        :param fields: ordered list of `(type_str, field_name)` tuples.
            The first field must be `("uint64_t", "timestamp")`.
        :param multi_id: instance id (0 = primary instance).
        """
        if fields[0] != ("uint64_t", "timestamp"):
            raise ValueError("The first field of every topic must be ('uint64_t', 'timestamp')")

        if name not in self._format_defs:
            self._format_defs[name] = fields

        msg_id = self._next_msg_id
        self._next_msg_id += 1
        handle = TopicHandle(
            name=name,
            multi_id=multi_id,
            msg_id=msg_id,
            field_names=[f_name for _, f_name in fields],
            field_types=[f_type for f_type, _ in fields],
        )
        self._topics[(name, multi_id)] = handle
        return handle

    def add_sample(self, topic: TopicHandle, timestamp_us: int, **field_values) -> None:
        """Add one data sample for a previously declared topic."""
        values = []
        for f_name, f_type in zip(topic.field_names, topic.field_types):
            if f_name == "timestamp":
                values.append(timestamp_us)
            else:
                values.append(field_values[f_name])
        payload = struct.pack("<H", topic.msg_id) + struct.pack(topic.struct_format, *values)
        self._data_section_messages.append(
            (timestamp_us, _message_header(payload, MSG_TYPE_DATA))
        )

    def add_truncated_sample(self, topic: TopicHandle, timestamp_us: int, **field_values) -> None:
        """Add a deliberately malformed (too-short) DATA message.

        Triggers pyulog's data-size mismatch check, which sets
        `ulog.file_corruption = True` -- used to test corruption handling
        deterministically without hand-crafting arbitrary invalid bytes.
        """
        values = []
        for f_name, f_type in zip(topic.field_names, topic.field_types):
            if f_name == "timestamp":
                values.append(timestamp_us)
            else:
                values.append(field_values[f_name])
        full_payload = struct.pack(topic.struct_format, *values)
        truncated_payload = full_payload[:-1]
        payload = struct.pack("<H", topic.msg_id) + truncated_payload
        self._data_section_messages.append(
            (timestamp_us, _message_header(payload, MSG_TYPE_DATA))
        )

    def add_log_message(self, timestamp_us: int, severity: str, text: str) -> None:
        """Add a plain (non-tagged) `log_message` entry."""
        log_level = LOG_LEVELS[severity]
        payload = struct.pack("<BQ", log_level, timestamp_us) + text.encode("utf-8")
        self._data_section_messages.append(
            (timestamp_us, _message_header(payload, MSG_TYPE_LOGGING))
        )

    def add_tagged_log_message(self, timestamp_us: int, tag: int, severity: str, text: str) -> None:
        """Add a tagged `logged_messages_tagged` entry."""
        log_level = LOG_LEVELS[severity]
        payload = struct.pack("<BHQ", log_level, tag, timestamp_us) + text.encode("utf-8")
        self._data_section_messages.append(
            (timestamp_us, _message_header(payload, MSG_TYPE_LOGGING_TAGGED))
        )

    def _build_format_messages(self) -> bytes:
        out = bytearray()
        for name, fields_def in self._format_defs.items():
            field_strs = "".join(f"{f_type} {f_name};" for f_type, f_name in fields_def)
            payload = f"{name}:{field_strs}".encode("utf-8")
            out.extend(_message_header(payload, MSG_TYPE_FORMAT))
        return bytes(out)

    def _build_subscription_messages(self) -> bytes:
        out = bytearray()
        for (name, multi_id), handle in self._topics.items():
            payload = struct.pack("<BH", multi_id, handle.msg_id) + name.encode("utf-8")
            out.extend(_message_header(payload, MSG_TYPE_ADD_LOGGED_MSG))
        return bytes(out)

    def build(self) -> bytes:
        """Assemble the full binary file content."""
        header = HEADER_BYTES + struct.pack("B", FILE_VERSION) + struct.pack("<Q", self.start_timestamp_us)

        definitions = self._build_format_messages()
        subscriptions = self._build_subscription_messages()

        ordered_data_messages = sorted(self._data_section_messages, key=lambda item: item[0])
        data_section = bytearray()
        data_section.extend(subscriptions)
        for _, raw in ordered_data_messages:
            data_section.extend(raw)

        return header + definitions + bytes(data_section)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.build())
        return path
