"""Tests for M5 message normalizers using source-independent sample records."""

import unittest
from datetime import datetime, timezone

from education import normalize_programme, normalize_programmes
from platform_analysis import normalize_feature_evidence, normalize_platform_evidence
from workforce import normalize_workforce_record, normalize_workforce_records


class EducationAgentTests(unittest.TestCase):
    """Check CYBERHEAD programme normalization and validation."""

    def test_normalizes_programme_to_schema_fields(self) -> None:
        message = normalize_programme(
            {
                "programme_id": "cyber-001",
                "country": "de",
                "name": " Cybersecurity MSc ",
                "institution": "Example University",
                "level": "Master",
                "ecsf_roles": ["Cybersecurity Implementer"],
                "url": "https://example.edu/programme",
            }
        )
        self.assertEqual(
            message,
            {
                "programme_id": "cyber-001",
                "country": "DE",
                "name": "Cybersecurity MSc",
                "institution": "Example University",
                "level": "master",
                "ecsf_roles": ["Cybersecurity Implementer"],
                "url": "https://example.edu/programme",
            },
        )

    def test_allows_unknown_level_to_be_omitted(self) -> None:
        message = normalize_programme(
            {
                "programme_id": "cyber-002",
                "country": "FR",
                "name": "Security course",
                "institution": "Example",
            }
        )
        self.assertNotIn("level", message)
        self.assertEqual(message["ecsf_roles"], [])

    def test_rejects_missing_or_invalid_programme_data(self) -> None:
        with self.assertRaises(ValueError):
            normalize_programme({"country": "DE"})
        with self.assertRaises(ValueError):
            normalize_programme(
                {
                    "programme_id": "1",
                    "country": "Germany",
                    "name": "Course",
                    "institution": "Example",
                }
            )
        with self.assertRaises(ValueError):
            normalize_programmes([{"programme_id": "bad"}])


class WorkforceAgentTests(unittest.TestCase):
    """Check extracted ISC2 workforce figures without filling missing values."""

    def test_normalizes_workforce_message_and_optional_figures(self) -> None:
        message = normalize_workforce_record(
            {
                "country": "de",
                "year": 2024,
                "workforce_size": 100_000,
                "workforce_gap": 20_000,
                "source": "isc2_2024",
            }
        )
        self.assertEqual(message["country"], "DE")
        self.assertEqual(message["workforce_size"], 100_000)
        self.assertEqual(
            normalize_workforce_records(
                [{"country": "FR", "year": 2024, "source": "isc2_2024"}]
            ),
            [{"country": "FR", "year": 2024, "source": "isc2_2024"}],
        )

    def test_rejects_invalid_workforce_values(self) -> None:
        base = {"country": "DE", "year": 2024, "source": "isc2_2024"}
        for invalid in (
            {**base, "year": True},
            {**base, "workforce_gap": -1},
            {**base, "workforce_size": 12.5},
            {**base, "country": "Germany"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_workforce_record(invalid)


class PlatformAnalysisAgentTests(unittest.TestCase):
    """Check reviewed platform evidence formatting and timestamp consistency."""

    def test_formats_evidence_with_an_explicit_utc_timestamp(self) -> None:
        timestamp = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)
        message = normalize_feature_evidence(
            {
                "platform": "CyberSeek",
                "feature": "Supply_Demand_Map",
                "supported": True,
                "notes": "Documented in the product overview.",
            },
            analysed_at=timestamp,
        )
        self.assertEqual(message["platform"], "cyberseek")
        self.assertEqual(message["feature"], "supply_demand_map")
        self.assertEqual(message["analysed_at"], "2026-10-27T08:00:00+00:00")
        self.assertTrue(message["supported"])

    def test_rejects_unknown_decisions_and_naive_timestamps(self) -> None:
        evidence = {"platform": "example", "feature": "feature", "supported": "yes"}
        with self.assertRaises(ValueError):
            normalize_feature_evidence(evidence)
        with self.assertRaises(ValueError):
            normalize_feature_evidence(
                {**evidence, "supported": False},
                analysed_at=datetime(2026, 10, 27),
            )

    def test_uses_one_timestamp_for_all_feature_rows(self) -> None:
        timestamp = datetime(2026, 10, 27, tzinfo=timezone.utc)
        messages = normalize_platform_evidence(
            [
                {"platform": "CyberSeek", "feature": "maps", "supported": True},
                {"platform": "ENISA ECSF", "feature": "roles", "supported": True},
            ],
            analysed_at=timestamp,
        )
        self.assertEqual(messages[0]["analysed_at"], messages[1]["analysed_at"])


if __name__ == "__main__":
    unittest.main()
