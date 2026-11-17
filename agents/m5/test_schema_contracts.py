"""Check M5 normalizer outputs against the checked-in JSON Schema contracts."""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ace import contracts
from education import normalize_programme
from m5_runtime import read_records
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
        schema_path = ROOT / "shared" / "schemas" / "raw_programme.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        role_schema = schema["properties"]["ecsf_roles"]["items"]
        self.assertEqual(role_schema["type"], "string")
        self.assertNotIn("enum", role_schema)
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
        contracts.validate("raw.programmes", message)
        self.assertIsNone(
            contracts.check(
                "raw.programmes",
                {**message, "ecsf_roles": ["Cybersecurity Operations"]},
            )
        )

    def test_workforce_output_matches_raw_workforce_contract(self) -> None:
        message = normalize_workforce_record(
            {
                "geography_level": "region",
                "geography": "Europe",
                "year": 2024,
                "workforce_size": 1000,
                "workforce_gap": 100,
                "source": "isc2_2024",
                "source_reference": "Figures 2 and 3",
            }
        )
        assert_contract_shape(self, "raw_workforce.schema.json", message)
        contracts.validate("raw.workforce", message)

    def test_isc2_2024_source_records_preserve_published_geography(self) -> None:
        records = read_records(
            ROOT / "agents" / "m5" / "sources" / "isc2_2024_global.json"
        )
        messages = [normalize_workforce_record(record) for record in records]

        for message in messages:
            assert_contract_shape(self, "raw_workforce.schema.json", message)
            contracts.validate("raw.workforce", message)

        self.assertEqual(len(messages), 6)
        europe = next(
            message for message in messages if message["geography"] == "Europe"
        )
        self.assertEqual(europe["workforce_size"], 1_300_023)
        self.assertEqual(europe["workforce_gap"], 392_320)
        self.assertTrue(all("country" not in message for message in messages))

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
