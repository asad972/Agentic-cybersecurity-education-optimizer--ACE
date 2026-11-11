"""Verify that reviewed platform evidence can be normalized to the shared contract."""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from ace import contracts
from ace.llm import BudgetExceededError, LLMClient
from platform_agent.__main__ import (
    EvidenceSummary,
    main as platform_main,
    normalize_record,
)
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

    def test_llm_summary_preserves_reviewed_decision_and_citation(self) -> None:
        evidence = {
            "platform": "CyberSeek",
            "feature": "career_pathway",
            "supported": True,
            "notes": "CyberSeek describes career pathways. Source: https://cyberseek.org/",
        }
        client = Mock(spec=LLMClient)
        client.generate.return_value = SimpleNamespace(
            value=EvidenceSummary(
                summary="The source describes cybersecurity career pathways."
            )
        )

        message = normalize_record(
            evidence,
            datetime(2026, 11, 2, tzinfo=timezone.utc),
            llm_client=client,
        )

        self.assertTrue(message["supported"])
        self.assertIn("AI-assisted summary (source-grounded)", message["notes"])
        self.assertIn("Source: https://cyberseek.org/", message["notes"])
        request = client.generate.call_args.kwargs
        self.assertEqual(request["task"], "platform_evidence_summary")
        self.assertEqual(request["prompt_version"], "v1")
        self.assertIs(request["response_model"], EvidenceSummary)
        contracts.validate("analysis.platforms", message)

    def test_budget_exhaustion_falls_back_to_cited_curated_evidence(self) -> None:
        evidence = {
            "platform": "CyberSeek",
            "feature": "career_pathway",
            "supported": True,
            "notes": "CyberSeek describes pathways. Source: https://cyberseek.org/",
        }
        client = Mock(spec=LLMClient)
        client.generate.side_effect = BudgetExceededError("budget exhausted")

        message = normalize_record(
            evidence,
            datetime(2026, 11, 2, tzinfo=timezone.utc),
            llm_client=client,
        )

        self.assertTrue(message["supported"])
        self.assertEqual(message["notes"], evidence["notes"])
        self.assertNotIn("AI-assisted summary", message["notes"])

    def test_llm_analysis_requires_cited_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires cited evidence"):
            normalize_record(
                {
                    "platform": "CyberSeek",
                    "feature": "career_pathway",
                    "supported": True,
                    "notes": "Uncited claim.",
                },
                datetime(2026, 11, 2, tzinfo=timezone.utc),
                llm_client=Mock(spec=LLMClient),
            )

    def test_platform_agent_reuses_one_llm_client_for_the_process(self) -> None:
        evidence = {
            "platform": "CyberSeek",
            "feature": "career_pathway",
            "supported": True,
            "notes": "CyberSeek describes pathways. Source: https://cyberseek.org/",
        }
        client = MagicMock(spec=LLMClient)
        client.__enter__.return_value = client
        client.generate.return_value = SimpleNamespace(
            value=EvidenceSummary(summary="The source describes career pathways.")
        )

        def process_with_run_agent(**kwargs):
            normalizer = kwargs["normalizer_factory"]()
            return [normalizer(evidence), normalizer(evidence)]

        client_factory = Mock(return_value=client)
        with (
            patch(
                "platform_agent.__main__.LLMClient.from_env", client_factory
            ),
            patch(
                "platform_agent.__main__.run_agent",
                side_effect=process_with_run_agent,
            ),
        ):
            platform_main()

        client_factory.assert_called_once_with()
        self.assertEqual(client.generate.call_count, 2)
        client.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
