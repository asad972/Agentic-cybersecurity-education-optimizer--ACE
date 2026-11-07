"""Tests for M5 canonical input loading and schema-routed Kafka publication."""

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from m5_runtime import publish_records, read_records
from education_agent.__main__ import normalize_input as normalize_programme_input
from platform_agent.__main__ import normalize_input as normalize_platform_input
from workforce_agent.__main__ import normalize_input as normalize_workforce_input


class RecordingProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, bytes]] = []

    def send(self, topic: str, *, key: bytes, value: bytes) -> "CompletedDelivery":
        self.sent.append((topic, key, value))
        return CompletedDelivery()


class CompletedDelivery:
    def get(self) -> None:
        return None


class FailedDelivery:
    def get(self) -> None:
        raise RuntimeError("Kafka delivery failed")


class M5RuntimeTests(unittest.TestCase):
    def test_reads_jsonl_and_csv_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jsonl = root / "records.jsonl"
            jsonl.write_text('{"country":"DE"}\n\n{"country":"FR"}\n', encoding="utf-8")
            csv_file = root / "records.csv"
            csv_file.write_text("country,year\nDE,2024\n", encoding="utf-8")

            self.assertEqual(read_records(jsonl), [{"country": "DE"}, {"country": "FR"}])
            self.assertEqual(
                read_records(csv_file), [{"country": "DE", "year": "2024"}]
            )

    def test_rejects_malformed_jsonl_with_line_number(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "records.jsonl"
            source.write_text('{"country":"DE"}\nnot-json\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, r"records\.jsonl:2"):
                read_records(source)

    def test_publication_uses_contract_topic_and_invalid_route(self) -> None:
        producer = RecordingProducer()
        messages: list[dict[str, Any]] = [
            {"country": "DE", "year": 2024, "source": "isc2_2024"},
            {"country": "Germany", "year": 2024, "source": "isc2_2024"},
        ]

        sent = publish_records("raw.workforce", "m5-test", messages, producer)

        self.assertEqual(sent, 2)
        self.assertEqual(producer.sent[0][0:2], ("raw.workforce", b"DE"))
        self.assertEqual(producer.sent[1][0], "errors.invalid")
        invalid = json.loads(producer.sent[1][2])
        self.assertEqual(invalid["topic"], "raw.workforce")
        self.assertEqual(invalid["producer"], "m5-test")

    def test_publication_propagates_kafka_delivery_failures(self) -> None:
        class FailingProducer:
            def send(self, topic: str, *, key: bytes, value: bytes) -> FailedDelivery:
                return FailedDelivery()

        with self.assertRaisesRegex(RuntimeError, "Kafka delivery failed"):
            publish_records(
                "raw.workforce",
                "m5-test",
                [{"country": "DE", "year": 2024, "source": "isc2_2024"}],
                FailingProducer(),
            )

    def test_agent_csv_inputs_are_converted_before_normalization(self) -> None:
        programmes = normalize_programme_input(
            [
                {
                    "programme_id": "cyber-001",
                    "country": "DE",
                    "name": "Cybersecurity",
                    "institution": "Example University",
                    "ecsf_roles": '["Cybersecurity Implementer"]',
                },
                {
                    "programme_id": "cyber-002",
                    "country": "FR",
                    "name": "Security",
                    "institution": "Example University",
                    "ecsf_roles": None,
                },
            ]
        )
        workforce = normalize_workforce_input(
            [{"country": "DE", "year": "2024", "workforce_size": "120", "source": "isc2_2024"}]
        )
        platforms = normalize_platform_input(
            [{"platform": "ENISA ECSF", "feature": "role profiles", "supported": "true"}]
        )

        self.assertEqual(programmes[0]["ecsf_roles"], ["Cybersecurity Implementer"])
        self.assertEqual(programmes[1]["ecsf_roles"], [])
        self.assertEqual(workforce[0]["workforce_size"], 120)
        self.assertTrue(platforms[0]["supported"])


if __name__ == "__main__":
    unittest.main()
