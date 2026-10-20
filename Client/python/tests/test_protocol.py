"""Tests for the synced client discovery protocol helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from core.constants import DEFAULT_TCP_PORT, MAIN_NODE_NAME, MDNS_SERVICE_TYPE  # noqa: E402
from wire.discovery_protocol import (  # noqa: E402
    build_announce_message,
    build_discover_message,
    describe_discovery_message,
    parse_announce_message,
    parse_discover_message,
)


class ProtocolTests(unittest.TestCase):
    def test_discover_round_trip(self) -> None:
        message = build_discover_message("termux superweb client")
        self.assertTrue(parse_discover_message(message))
        self.assertEqual(
            describe_discovery_message(message),
            f"mDNS PTR query for {MDNS_SERVICE_TYPE}",
        )

    def test_announce_round_trip(self) -> None:
        message = build_announce_message("10.0.0.5", DEFAULT_TCP_PORT, MAIN_NODE_NAME)
        payload = parse_announce_message(message)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.host, "10.0.0.5")
        self.assertEqual(payload.port, DEFAULT_TCP_PORT)
        self.assertEqual(payload.node_name, MAIN_NODE_NAME)
        self.assertIn(MAIN_NODE_NAME, describe_discovery_message(message))


if __name__ == "__main__":
    unittest.main()
