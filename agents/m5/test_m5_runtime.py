"""Tests for M5 canonical input loading and schema-routed Kafka publication."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from m5_runtime import (
    publish_normalized_records,
    publish_records,
    read_records,
    run_agent,
)
from education_agent.__main__ import main as education_main
from education_agent.__main__ import normalize_record as normalize_programme_record
from platform_agent.__main__ import normalize_record as normalize_platform_record
from workforce_agent.__main__ import normalize_record as normalize_workforce_record


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
    def test_run_agent_uses_the_shared_cli_pipeline(self) -> None:
        records = [{"country": "DE"}]

        def normalizer(record: dict[str, str]) -> dict[str, str]:
            return record

        with (
            patch.object(sys, "argv", ["m5-agent", "--input", "input.json"]),
            patch("m5_runtime.read_records", return_value=records) as read,
            patch("m5_runtime.publish_normalized_file", return_value=1) as publish,
        ):
            run_agent(
                topic="raw.workforce",
                producer_name="m5-test",
                normalizer_factory=lambda: normalizer,
                input_help="input file",
                output_label="record",
            )

        read.assert_called_once_with("input.json")
        publish.assert_called_once_with(
            "raw.workforce", "m5-test", records, normalizer
        )

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

    def test_bad_rows_route_to_invalid_without_dropping_good_rows(self) -> None:
        producer = RecordingProducer()
        rows = [
            {
                "programme_id": "good",
                "country": "DE",
                "name": "Security",
                "institution": "Example University",
                "level": "master",
                "ecsf_roles": [],
            },
            {
                "programme_id": "bad",
                "country": "FR",
                "name": "Security",
                "institution": "Example University",
                "level": "diploma",
                "ecsf_roles": [],
            },
        ]

        sent = publish_normalized_records(
            "raw.programmes",
            "m5-education",
            rows,
            normalize_programme_record,
            producer,
        )

        self.assertEqual(sent, 2)
        self.assertEqual(
            [message[0] for message in producer.sent],
            ["raw.programmes", "errors.invalid"],
        )
        invalid = json.loads(producer.sent[1][2])
        self.assertIn("row 2", invalid["reason"])
        self.assertEqual(json.loads(invalid["payload"]), rows[1])

    def test_invalid_csv_integer_routes_to_invalid_and_keeps_good_workforce_row(self) -> None:
        producer = RecordingProducer()
        rows = [
            {"country": "DE", "year": "2024", "workforce_size": "12000", "source": "isc2_2024"},
            {"country": "FR", "year": "2024", "workforce_size": "12 345", "source": "isc2_2024"},
        ]

        publish_normalized_records(
            "raw.workforce",
            "m5-workforce",
            rows,
            normalize_workforce_record,
            producer,
        )

        self.assertEqual(
            [message[0] for message in producer.sent],
            ["raw.workforce", "errors.invalid"],
        )
        invalid = json.loads(producer.sent[1][2])
        self.assertIn("row 2", invalid["reason"])

    def test_agent_csv_inputs_are_converted_before_normalization(self) -> None:
        programme = normalize_programme_record(
            {
                "programme_id": "cyber-001",
                "country": "DE",
                "name": "Cybersecurity",
                "institution": "Example University",
                "ecsf_roles": '["Cybersecurity Implementer"]',
            }
        )
        workforce = normalize_workforce_record(
            {"country": "DE", "year": "2024", "workforce_size": "120", "source": "isc2_2024"}
        )

        self.assertEqual(programme["ecsf_roles"], ["Cybersecurity Implementer"])
        self.assertEqual(workforce["workforce_size"], 120)
        timestamp = datetime(2026, 11, 8, tzinfo=timezone.utc)
        platform = normalize_platform_record(
            {"platform": "ENISA ECSF", "feature": "role profiles", "supported": "true"},
            timestamp,
        )
        self.assertEqual(platform["analysed_at"], "2026-11-08T00:00:00+00:00")

    def test_education_agent_normalizes_reviewed_roles_without_llm(self) -> None:
        records = [
            {
                "programme_id": "cyber-mapped-001",
                "country": "DE",
                "name": "Cybersecurity",
                "institution": "Example University",
                "ecsf_roles": ["Cybersecurity Educator"],
            }
        ]
        with patch("education_agent.__main__.run_agent") as run:
            education_main()
            normalizer = run.call_args.kwargs["normalizer_factory"]()
            message = normalizer(records[0])

        self.assertEqual(message["ecsf_roles"], ["Cybersecurity Educator"])
        self.assertEqual(
            normalize_programme_record(
                {
                    "programme_id": "cyber-unmapped-001",
                    "country": "DE",
                    "name": "Cybersecurity",
                    "institution": "Example University",
                }
            )["ecsf_roles"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
