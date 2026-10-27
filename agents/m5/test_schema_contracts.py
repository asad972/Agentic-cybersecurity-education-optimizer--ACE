"""Check M5 normalizer outputs against the checked-in JSON Schema contracts."""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from education import normalize_programme
from platform_analysis import normalize_feature_evidence
from workforce import normalize_workforce_record


ROOT = Path(__file__).resolve().parents[2]


def assert_contract_shape(
    test: unittest.TestCase, schema_name: str, message: dict[str, Any]
) -> None:
    """Check required and additional-property rules from a JSON Schema file."""
    schema_path = ROOT / "shared" / "schemas" / schema_name
    with schema_path.open(encoding="utf-8") as schema_file:
        schema = json.load(schema_file)

    required = set(schema.get("required", []))
    properties = set(schema.get("properties", {}))
    test.assertTrue(required.issubset(message.keys()))
    if schema.get("additionalProperties") is False:
        test.assertTrue(set(message.keys()).issubset(properties))


class M5SchemaContractTests(unittest.TestCase):
    """Keep normalizer output keys aligned with the shared message schemas."""

    def test_education_output_matches_raw_programme_contract(self) -> None:
        message = normalize_programme(
            {
                "programme_id": "sample-1",
                "country": "DE",
                "name": "Cybersecurity",
                "institution": "Example University",
                "level": "master",
                "ecsf_roles": ["Cybersecurity Implementer"],
                "url": "https://example.test/course",
            }
        )
        assert_contract_shape(self, "raw_programme.schema.json", message)

    def test_workforce_output_matches_raw_workforce_contract(self) -> None:
        message = normalize_workforce_record(
            {
                "country": "DE",
                "year": 2024,
                "workforce_size": 1000,
                "workforce_gap": 100,
                "source": "isc2_2024",
            }
        )
        assert_contract_shape(self, "raw_workforce.schema.json", message)

    def test_platform_output_matches_platform_analysis_contract(self) -> None:
        message = normalize_feature_evidence(
            {
                "platform": "cyberseek",
                "feature": "supply_demand_map",
                "supported": True,
            },
            analysed_at=datetime(2026, 10, 27, tzinfo=timezone.utc),
        )
        assert_contract_shape(self, "platform_analysis.schema.json", message)


if __name__ == "__main__":
    unittest.main()
