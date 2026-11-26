"""Run or score the multilingual S2 evaluation without Kafka or Flink."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile
from typing import Any

from ace.llm import BudgetExceededError, LLMClient, LLMError, LLMResult, LLMSettings
from ace.taxonomy import ExtractionResult, Taxonomy, load_taxonomy
from s2_skill_extraction.llm_extractor import (
    DEFAULT_MAX_TOKENS,
    ExtractedMentions,
    LLMSkillExtractor,
    PROMPT_VERSION,
)

from .benchmark import Ad, LANGUAGES, MODES, paired_interval, read_corpus, recommend, score, validate_labels


ROOT = Path(__file__).resolve().parents[3]
CORPUS = Path(__file__).with_name("ads.jsonl")
MODELS = {"local": "qwen3:4b", "hosted": "claude-haiku-5-5"}
SMOKE_AD = Ad(
    "en-smoke", "en", "Security engineer",
    "Maintain Firewalls and Terraform. Experience with alert enrichment is helpful.",
    {"skills": ["Firewalls", "Terraform"], "certifications": [], "role": None, "unmapped": []},
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        pending = Path(temporary.name)
        try:
            json.dump(value, temporary, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.close()
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)


def manifest(corpus: Path, taxonomy_dir: Path, *, smoke: bool = False) -> dict[str, Any]:
    return {
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "taxonomy_sha256": {
            name: sha256((taxonomy_dir / name).read_bytes()).hexdigest()
            for name in ("skills.csv", "certifications.csv", "ecsf_roles.csv")
        },
        "corpus_sha256": sha256(corpus.read_bytes()).hexdigest(),
        "prompt_version": PROMPT_VERSION,
        "max_output_tokens": DEFAULT_MAX_TOKENS,
        "sample": "smoke" if smoke else "benchmark",
    }


def labels(result: ExtractionResult) -> dict[str, Any]:
    return {
        "skills": list(result.skills),
        "certifications": list(result.certifications),
        "unmapped": list(result.unmapped),
        "role": result.ecsf_role,
    }


class CallCapture:
    """Capture metadata while S2 consumes the unmodified client result."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client
        self.last: LLMResult[ExtractedMentions] | None = None

    def generate(self, **kwargs: Any) -> LLMResult[ExtractedMentions]:
        self.last = self.client.generate(**kwargs)
        return self.last


def store_spend(client: LLMClient, day: str) -> Decimal:
    snapshot = client.store.budget_snapshot(date.fromisoformat(day))
    return snapshot.spent_eur if snapshot is not None else Decimal(0)


def evaluate(
    ads: list[Ad],
    taxonomy: Taxonomy,
    checkpoint: dict[str, Any],
    save: Callable[[dict[str, Any]], None],
    client: LLMClient | None = None,
) -> bool:
    mode = checkpoint["mode"]
    if (client is None) != (mode == "baseline"):
        raise ValueError("only the taxonomy baseline runs without an LLM client")
    capture = CallCapture(client) if client is not None else None
    extractor = LLMSkillExtractor(capture, fallback_to_taxonomy=False) if capture else None
    rows = checkpoint["rows"]
    if set(rows) - {ad.id for ad in ads}:
        raise ValueError("checkpoint contains an unknown ad ID")
    started = checkpoint.setdefault("opening_spent_eur_by_utc_day", {})
    settled = checkpoint.setdefault("settled_spend_eur_by_utc_day", {})

    for ad in ads:
        old = rows.get(ad.id, {})
        if old.get("status") == "ok":
            validate_labels(old["labels"], taxonomy)
            continue
        day = datetime.now(UTC).date().isoformat()
        if mode == "hosted" and client is not None and day not in started:
            started[day] = str(store_spend(client, day))
            save(checkpoint)
        row = {"attempts": old.get("attempts", 0) + 1, "errors": list(old.get("errors", []))}
        try:
            if extractor is None:
                extracted = taxonomy.extract_text(ad.description, title=ad.title)
            else:
                assert capture is not None
                capture.last = None
                extracted = extractor.extract(
                    "jobs.clean", {"job_title": ad.title, "description": ad.description}, taxonomy
                )
                result = capture.last
                if result is None or extractor.last_call_fell_back:
                    raise ValueError("S2 did not return a live provider result")
                row["usage"] = {
                    "cached": result.cached,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "response_cost_eur": str(result.cost_eur),
                }
            predicted = labels(extracted)
            validate_labels(predicted, taxonomy)
            row.update(status="ok", labels=predicted)
        except LLMError as error:
            row["status"] = "error"
            row["errors"].append(type(error).__name__)
        rows[ad.id] = row
        if mode == "hosted" and client is not None:
            end_day = datetime.now(UTC).date().isoformat()
            # This store is dedicated to one benchmark process. A new UTC day
            # reached during a call starts at zero.
            started.setdefault(end_day, "0")
            for spent_day, opening in started.items():
                delta = store_spend(client, spent_day) - Decimal(opening)
                if delta < 0:
                    raise ValueError("hosted checkpoint requires the original Cassandra store")
                settled[spent_day] = str(delta)
        save(checkpoint)
        if row["status"] == "error":
            print(f"{mode} {ad.id} {row['errors'][-1]}", file=sys.stderr, flush=True)
            if row["errors"][-1] == BudgetExceededError.__name__:
                break
        else:
            print(f"{mode} {ad.id} ok", file=sys.stderr, flush=True)

    return all(rows.get(ad.id, {}).get("status") == "ok" for ad in ads)


def run(args: argparse.Namespace, taxonomy: Taxonomy) -> int:
    ads = [SMOKE_AD] if args.smoke else read_corpus(args.corpus, taxonomy)
    identity = manifest(args.corpus, args.taxonomy, smoke=args.smoke)
    model = MODELS.get(args.mode)
    configuration = None
    env = {**os.environ, "LLM_PROVIDER": args.mode}
    if model:
        settings = LLMSettings.from_env(env)
        configured_model = settings.local_model if args.mode == "local" else settings.hosted_model
        if configured_model != model:
            raise ValueError(f"{args.mode} benchmark requires model {model}")
        if settings.max_tokens < DEFAULT_MAX_TOKENS:
            raise ValueError("configured output token cap is below S2's output token limit")
        if args.mode == "hosted":
            if settings.daily_budget_eur > Decimal("2"):
                raise ValueError("the hosted evaluation cap is at most EUR 2 per UTC day")
            configuration = {
                "cassandra_keyspace": env.get("CASSANDRA_KEYSPACE", "ace"),
                "daily_budget_eur": str(settings.daily_budget_eur),
                "input_eur_per_mtok": str(settings.hosted_input_eur_per_mtok),
                "output_eur_per_mtok": str(settings.hosted_output_eur_per_mtok),
            }
    header = {"study": identity, "mode": args.mode, "model": model, "configuration": configuration}
    checkpoint = read_json(args.output) if args.output.exists() else {**header, "rows": {}}
    if any(checkpoint.get(key) != value for key, value in header.items()):
        raise ValueError("checkpoint metadata differs from the current benchmark inputs")
    context = LLMClient.from_env(environ=env) if model else nullcontext(None)
    with context as client:
        complete = evaluate(
            ads, taxonomy, checkpoint, lambda value: write_json(args.output, value), client
        )
    return 0 if complete else 1


def coverage(checkpoint: dict[str, Any], ads: list[Ad]) -> dict[str, Any]:
    rows = checkpoint["rows"]
    completed = [
        ad.id for ad in ads if rows.get(ad.id, {}).get("status") == "ok"
    ]
    usage = [rows[ad_id]["usage"] for ad_id in completed if "usage" in rows[ad_id]]
    settled = checkpoint.get("settled_spend_eur_by_utc_day", {})
    return {
        "model": checkpoint["model"],
        "completed": len(completed),
        "expected": len(ads),
        "failed": {ad_id: row["errors"][-1] for ad_id, row in rows.items() if row["status"] == "error"},
        "missing": [ad.id for ad in ads if ad.id not in rows],
        "recovered_errors": sum(len(rows[ad_id]["errors"]) for ad_id in completed),
        "cache_hits": sum(row["cached"] for row in usage),
        "input_tokens": sum(row["input_tokens"] or 0 for row in usage),
        "output_tokens": sum(row["output_tokens"] or 0 for row in usage),
        "cached_response_cost_eur": str(sum(
            (Decimal(row["response_cost_eur"]) for row in usage if row["cached"]), Decimal(0)
        )),
        "settled_spend_eur": str(sum(map(Decimal, settled.values()), Decimal(0))),
        "settled_spend_eur_by_utc_day": settled,
    }


def summarize(ads: list[Ad], checkpoints: dict[str, dict[str, Any]]) -> dict[str, Any]:
    predictions = {
        mode: {ad_id: row["labels"] for ad_id, row in checkpoint["rows"].items() if row["status"] == "ok"}
        for mode, checkpoint in checkpoints.items()
    }
    common = [ad for ad in ads if all(ad.id in items for items in predictions.values())]
    scopes = {"overall": common, **{
        lang: [ad for ad in common if ad.language == lang] for lang in LANGUAGES
    }}
    scores = {
        mode: {name: score(scope, items) for name, scope in scopes.items()}
        for mode, items in predictions.items()
    }

    def f1_delta(mode: str, reference: str, name: str) -> float | None:
        left = scores[mode][name]["skills"]["f1"]
        right = scores[reference][name]["skills"]["f1"]
        return left - right if left is not None and right is not None else None

    gains = {
        mode: {name: f1_delta(mode, "baseline", name) for name in scopes}
        for mode in predictions if mode != "baseline"
    }
    comparison = None
    if set(predictions) == set(MODES) and len(common) == len(ads):
        differences = {name: f1_delta("hosted", "local", name) for name in scopes}
        if all(value is not None for value in differences.values()):
            interval = paired_interval(common, predictions["local"], predictions["hosted"])
            comparison = {
                "hosted_skill_f1_gain": differences,
                "interval_95": interval,
                "recommendation": recommend(differences["overall"], interval["lower"], differences),
            }
    return {
        "study": checkpoints["baseline"]["study"],
        "paired_ads": len(common),
        "coverage": {
            mode: coverage(checkpoints[mode], ads) if mode in checkpoints else None
            for mode in MODES
        },
        "scores_on_common_ads": scores,
        "skill_f1_gains_over_baseline": gains,
        "final_comparison": comparison,
    }


def report(args: argparse.Namespace, taxonomy: Taxonomy) -> int:
    ads = read_corpus(args.corpus, taxonomy)
    checkpoints = {}
    expected = manifest(args.corpus, args.taxonomy)
    expected.pop("revision")
    for mode in MODES:
        path = getattr(args, mode)
        if path is None:
            continue
        checkpoint = read_json(path)
        identity = checkpoint["study"]
        if (
            checkpoint["mode"] != mode
            or checkpoint["model"] != MODELS.get(mode)
            or any(identity.get(key) != value for key, value in expected.items())
            or (checkpoints and identity != checkpoints["baseline"]["study"])
        ):
            raise ValueError("checkpoints must match the corpus, prompt, taxonomy and evaluated revision")
        if set(checkpoint["rows"]) - {ad.id for ad in ads}:
            raise ValueError("checkpoint contains an unknown ad ID")
        for row in checkpoint["rows"].values():
            if row["status"] == "ok":
                validate_labels(row["labels"], taxonomy)
        checkpoints[mode] = checkpoint
    write_json(args.output, summarize(ads, checkpoints))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_command = commands.add_parser("run")
    run_command.add_argument("--mode", choices=MODES, required=True)
    run_command.add_argument("--smoke", action="store_true")
    score_command = commands.add_parser("score")
    for mode in MODES:
        score_command.add_argument(f"--{mode}", type=Path, required=mode == "baseline")
    for command in (run_command, score_command):
        command.add_argument("--corpus", type=Path, default=CORPUS)
        command.add_argument("--taxonomy", type=Path, default=ROOT / "shared" / "taxonomy")
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        taxonomy = load_taxonomy(args.taxonomy)
        return run(args, taxonomy) if args.command == "run" else report(args, taxonomy)
    except (ValueError, LLMError, OSError) as error:
        print(f"Evaluation stopped: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
