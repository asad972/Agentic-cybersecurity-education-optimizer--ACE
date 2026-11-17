"""Tests for M5 message normalizers using source-independent sample records."""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import zipfile
from types import SimpleNamespace

from ace import contracts
from education import ECSF_ROLES, normalize_programme, normalize_programmes
from education_agent.__main__ import (
    normalize_enisa_record,
    normalize_record as normalize_exported_programme,
)
from m5_runtime import read_records
from platform_analysis import normalize_feature_evidence, normalize_platform_evidence
from workforce import normalize_workforce_record, normalize_workforce_records
from prepare_cyberhead import prepare_records, read_docx_rows


class EducationAgentTests(unittest.TestCase):
    """Check CYBERHEAD programme normalization and validation."""

    def test_imports_synthetic_exports_without_claiming_ecsf_roles(self) -> None:
        testdata = Path(__file__).parent / "testdata"
        exports = (
            ("sample_cyberhead_export.json", "programmes"),
            ("sample_cyberhead_export.jsonl", None),
            ("sample_cyberhead_export.csv", None),
        )

        for filename, records_key in exports:
            with self.subTest(filename=filename):
                records = read_records(
                    testdata / filename, records_key=records_key
                )
                messages = [
                    normalize_exported_programme(record) for record in records
                ]

                self.assertEqual(
                    [
                        (message["programme_id"], message["country"])
                        for message in messages
                    ],
                    [("AT-001", "AT"), ("DE-001", "DE"), ("NL-001", "NL")],
                )
                for message in messages:
                    self.assertEqual(message["ecsf_roles"], [])
                    contracts.validate("raw.programmes", message)

    def test_demo_role_categories_are_not_emitted_as_ecsf_roles(self) -> None:
        testdata = Path(__file__).parent / "testdata"
        categories = json.loads(
            (testdata / "synthetic_role_categories.json").read_text(
                encoding="utf-8"
            )
        )["categories"]
        records = read_records(
            testdata / "sample_cyberhead_export.json",
            records_key="programmes",
        )
        messages = [normalize_exported_programme(record) for record in records]
        demo_labels = set(categories)

        self.assertTrue(demo_labels)
        self.assertTrue(
            all(
                not (set(message["ecsf_roles"]) & demo_labels)
                for message in messages
            )
        )

    def test_maps_public_enisa_programme_and_validates_the_contract(self) -> None:
        class FakeLLM:
            def __init__(self) -> None:
                self.request = None

            def generate(self, **kwargs):
                self.request = kwargs
                return SimpleNamespace(
                    value=SimpleNamespace(
                        ecsf_roles=[
                            "Cyber Incident Responder",
                            "Cybersecurity Researcher",
                        ]
                    )
                )

        llm = FakeLLM()
        message = normalize_enisa_record(
            {
                "ID": "programme-123",
                "state": {"public": True, "state": "approved"},
                "fields": {
                    "programme_name": "Cybersecurity MSc",
                    "programme_description": "Prepares incident responders.",
                    "institution_name": "Example University",
                    "country": "esp",
                    "programme_type": "Master Degree",
                    "related_urls": [
                        {"kind": "Website", "link": "https://example.edu/course"}
                    ],
                },
            },
            llm,
        )

        self.assertEqual(message["programme_id"], "programme-123")
        self.assertEqual(message["country"], "ES")
        self.assertEqual(message["level"], "master")
        self.assertEqual(
            message["ecsf_roles"],
            ["Cyber Incident Responder", "Cybersecurity Researcher"],
        )
        self.assertEqual(llm.request["task"], "programme_roles")
        contracts.validate("raw.programmes", message)

    def test_enisa_programme_without_description_does_not_call_llm(self) -> None:
        class UnexpectedLLM:
            def generate(self, **_kwargs):
                self.fail("LLM must not be called without programme evidence")

            def fail(self, message):
                raise AssertionError(message)

        message = normalize_enisa_record(
            {
                "ID": "programme-no-description",
                "fields": {
                    "programme_name": "Programme",
                    "programme_description": "",
                    "institution_name": "Example University",
                    "country": "deu",
                    "programme_type": "Postgraduate Course",
                    "related_urls": [],
                },
            },
            UnexpectedLLM(),
        )
        self.assertEqual(message["ecsf_roles"], [])
        self.assertEqual(message["level"], "other")

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
            normalize_programme(
                {
                    "programme_id": "unicode-country",
                    "country": "ßA",
                    "name": "Course",
                    "institution": "Example",
                }
            )
        with self.assertRaises(ValueError):
            normalize_programmes([{"programme_id": "bad"}])

    def test_accepts_the_twelve_canonical_ecsf_roles(self) -> None:
        self.assertEqual(len(ECSF_ROLES), 12)
        for role in ECSF_ROLES:
            with self.subTest(role=role):
                message = normalize_programme(
                    {
                        "programme_id": "canonical-role",
                        "country": "DE",
                        "name": "Course",
                        "institution": "Example University",
                        "ecsf_roles": [role],
                    }
                )
                self.assertEqual(message["ecsf_roles"], [role])
                contracts.validate("raw.programmes", message)

    def test_programme_can_have_multiple_reviewed_ecsf_roles(self) -> None:
        roles = ["Cybersecurity Architect", "Cybersecurity Implementer"]
        message = normalize_programme(
            {
                "programme_id": "multi-role-course",
                "country": "DE",
                "name": "Cloud Security",
                "institution": "Example University",
                "ecsf_roles": roles,
            }
        )

        self.assertEqual(message["ecsf_roles"], roles)

    def test_rejects_noncanonical_ecsf_labels(self) -> None:
        record = {
            "programme_id": "invalid-role",
            "country": "DE",
            "name": "Course",
            "institution": "Example University",
            "ecsf_roles": ["Cyber Security Analyst"],
        }
        with self.assertRaisesRegex(ValueError, "non-canonical ECSF"):
            normalize_programme(record)

    def test_rejects_duplicate_ecsf_roles(self) -> None:
        record = {
            "programme_id": "duplicate-role",
            "country": "DE",
            "name": "Course",
            "institution": "Example University",
            "ecsf_roles": ["Cybersecurity Educator", "Cybersecurity Educator"],
        }
        with self.assertRaisesRegex(ValueError, "duplicate roles"):
            normalize_programme(record)

    def test_prepares_docx_rows_without_mistaking_job_titles_for_ecsf_roles(self) -> None:
        document_xml = """\
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body><w:tbl>
            <w:tr>
              <w:tc><w:p><w:r><w:t>Name</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Abschlussgrad</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>PorgrammeId</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Institution</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Land</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>URL</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Lehrplan-/Skill-Information</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Kompatible rollen</w:t></w:r></w:p></w:tc>
            </w:tr>
            <w:tr>
              <w:tc><w:p><w:r><w:t>Cybersecurity</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>M. Sc.</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>programme-001</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Example University</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Deutschland</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>https://example.edu/course</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Cloud security, SIEM</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>SOC Analyst</w:t></w:r></w:p></w:tc>
            </w:tr>
            <w:tr>
              <w:tc><w:p><w:r><w:t>Regional programme</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>M. Sc.</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>programme-eu</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>EU Consortium</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Europäisches Gemeinschaftsprogramm</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>https://example.eu/course</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Governance</w:t></w:r></w:p></w:tc>
              <w:tc><w:p><w:r><w:t>Security Consultant</w:t></w:r></w:p></w:tc>
            </w:tr>
          </w:tbl></w:body>
        </w:document>
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cyberhead.docx"
            with zipfile.ZipFile(path, "w") as document:
                document.writestr("word/document.xml", document_xml)
            rows = read_docx_rows(path)

        review, programmes = prepare_records(rows, "2026-11-16")

        self.assertEqual(len(review), 2)
        self.assertEqual(len(programmes), 1)
        self.assertEqual(programmes[0]["country"], "DE")
        self.assertEqual(programmes[0]["level"], "master")
        self.assertEqual(programmes[0]["ecsf_roles"], [])
        self.assertEqual(review[0]["compatible_roles"], "SOC Analyst")
        self.assertEqual(review[0]["curriculum"], "Cloud security, SIEM")
        self.assertIn("country is not a recognized", review[1]["validation_errors"][0])

    def test_preparation_does_not_invent_access_date_or_silently_duplicate_ids(self) -> None:
        row = {
            "name": "Course",
            "level": "M. Sc.",
            "programme_id": "same-id",
            "institution": "University",
            "country": "Spain",
            "url": "https://example.edu/course",
            "curriculum": "",
            "compatible_roles": "Cybersecurity Analyst",
            "cyberhead_url": "",
            "_row": "2",
        }

        review, programmes = prepare_records([row, {**row, "_row": "3"}])

        self.assertIsNone(review[0]["accessed_on"])
        self.assertEqual(len(programmes), 1)
        self.assertIn("duplicate programme_id", review[1]["validation_errors"])
        self.assertEqual(review[0]["ecsf_roles"], [])

    def test_rejects_invalid_access_date(self) -> None:
        with self.assertRaisesRegex(ValueError, "ISO 8601 date"):
            prepare_records([], "yesterday")
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            prepare_records([], "20261116")


class WorkforceAgentTests(unittest.TestCase):
    """Check extracted ISC2 workforce figures without filling missing values."""

    def test_normalizes_workforce_message_and_optional_figures(self) -> None:
        message = normalize_workforce_record(
            {
                "geography_level": "country",
                "geography": "de",
                "year": 2024,
                "workforce_size": 100_000,
                "workforce_gap": 20_000,
                "source": "isc2_2024",
            }
        )
        self.assertEqual(message["geography_level"], "country")
        self.assertEqual(message["geography"], "DE")
        self.assertEqual(message["workforce_size"], 100_000)
        self.assertEqual(
            normalize_workforce_records(
                [
                    {
                        "country": "FR",
                        "year": 2024,
                        "source": "isc2_2024",
                    }
                ]
            ),
            [
                {
                    "geography_level": "country",
                    "geography": "FR",
                    "year": 2024,
                    "source": "isc2_2024",
                }
            ],
        )

    def test_normalizes_published_regional_estimates_and_growth_rates(self) -> None:
        message = normalize_workforce_record(
            {
                "geography_level": "region",
                "geography": "Europe",
                "year": 2024,
                "workforce_size": 1_300_023,
                "workforce_gap": 392_320,
                "workforce_growth_percent": -0.7,
                "workforce_gap_growth_percent": 12.8,
                "source": "isc2_2024",
                "source_reference": "Figures 2 and 3",
            }
        )

        self.assertEqual(message["geography"], "Europe")
        self.assertEqual(message["workforce_size"], 1_300_023)
        self.assertEqual(message["workforce_gap"], 392_320)
        self.assertEqual(message["workforce_growth_percent"], -0.7)
        self.assertEqual(message["workforce_gap_growth_percent"], 12.8)

    def test_rejects_invalid_workforce_values(self) -> None:
        base = {
            "geography_level": "country",
            "geography": "DE",
            "year": 2024,
            "source": "isc2_2024",
        }
        for invalid in (
            {**base, "year": True},
            {**base, "workforce_gap": -1},
            {**base, "workforce_size": 12.5},
            {**base, "geography": "Germany"},
            {**base, "geography": "ßA"},
            {**base, "geography": "EU"},
            {**base, "workforce_growth_percent": float("inf")},
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
