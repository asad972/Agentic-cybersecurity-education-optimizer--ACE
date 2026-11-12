"""Verify that reviewed platform evidence can be normalized to the shared contract."""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ace import contracts
from platform_analysis import normalize_platform_evidence


class PlatformEvidenceTests(unittest.TestCase):
    """Keep the M5 platform comparison evidence-backed and schema-shaped."""

    def test_curated_rows_have_sources_and_normalize(self) -> None:
        evidence_path = Path(__file__).with_name("platform_evidence.json")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        timestamp = datetime(2026, 11, 2, tzinfo=timezone.utc)

        messages = normalize_platform_evidence(evidence, analysed_at=timestamp)

        self.assertEqual(len(messages), 4)
        self.assertEqual(
            {message["platform"] for message in messages},
            {"cyberseek", "enisa_ecsf", "isc2_workforce_study"},
        )
        self.assertTrue(all(message["supported"] is True for message in messages))
        self.assertTrue(
            all("Source:" in message.get("notes", "") for message in messages)
        )
        self.assertEqual(
            {message["analysed_at"] for message in messages},
            {"2026-11-02T00:00:00+00:00"},
        )
        for message in messages:
            contracts.validate("analysis.platforms", message)


if __name__ == "__main__":
    unittest.main()
