import argparse
import json
import logging
import os
import time

from ace import contracts

from .collector import CollectorStore, ThreatCollector
from .fixtures import fixture_events
from .runtime import EVENTS_TOPIC, TASKS_TOPIC, KafkaTransport, Publisher, create_consumer, validate_task
from .sources import AbuseIPDB, OTX
from .utils import country_code, write_jsonl


def process_task(message, collector, publisher, flush):
    """Return only after delivery; caller commits the Kafka input after success."""
    try:
        task = json.loads(message.value())
        validate_task(task, message.key() or b"")
    except (contracts.InvalidMessage, ValueError, UnicodeError, TypeError) as error:
        rejected = contracts.invalid_record(TASKS_TOPIC, "threat-agent", str(error),
            (message.value() or b"").decode("utf-8", errors="replace"))
        publisher.publish(contracts.INVALID_TOPIC, rejected)
        flush()
        return
    # Provider/delivery failures propagate, leaving this task uncommitted.
    collector.handle_task(task, message.key() or b"")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only threat feed collector; fixture mode needs no secrets")
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--fixture-hours", type=int, default=168)
    parser.add_argument("--output", default="data/threat/events.jsonl")
    parser.add_argument("--publish", action="store_true", help="Publish fixture events to Kafka")
    parser.add_argument("--source", choices=["otx", "abuseipdb", "both"], default="both")
    parser.add_argument("--state", default=os.environ.get("THREAT_COLLECTOR_STATE", "data/threat/collector.sqlite"))
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--max-indicators", type=int, default=100, help="OTX indicator budget per batch (1..1000)")
    parser.add_argument("--abuse-limit", type=int, default=100, help="AbuseIPDB snapshot limit (1..1000)")
    parser.add_argument("--country")
    parser.add_argument("--once", action="store_true", help="Run one bounded live collection batch and exit")
    parser.add_argument("--consume-tasks", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=21600, help="Scheduled polling interval; default 6 hours")
    args = parser.parse_args(argv)
    if not 1 <= args.hours <= 24 or not 1 <= args.max_pages <= 10 or args.poll_seconds < 1:
        parser.error("hours=1..24, max-pages=1..10, poll-seconds>=1 required")
    if not 1 <= args.max_indicators <= 1000 or not 1 <= args.abuse_limit <= 1000:
        parser.error("max-indicators and abuse-limit must be 1..1000")
    if not 1 <= args.fixture_hours <= 744:
        parser.error("fixture-hours must be 1..744")
    if args.publish and not args.fixture:
        parser.error("--publish is only used with --fixture; live mode always publishes")
    if args.country:
        try:
            args.country = country_code(args.country)
        except ValueError as error:
            parser.error(str(error))
    logging.basicConfig(level=logging.INFO)
    events = fixture_events(args.fixture_hours) if args.fixture else None
    if events is not None:
        for row in events:
            contracts.validate(EVENTS_TOPIC, row)
        write_jsonl(args.output, events)
        print(f"Wrote {len(events)} synthetic observations to {args.output}")
        if not args.publish:
            return
    # Fail early before opening resources if a requested feed has no credentials.
    if events is None:
        for source, name in (("otx", "OTX_API_KEY"), ("abuseipdb", "ABUSEIPDB_API_KEY")):
            if args.source in {source, "both"} and not os.environ.get(name):
                parser.error(f"{name} is required for --source {args.source}")
    sources, store, consumer = [], None, None
    try:
        if events is None:
            if args.source in {"otx", "both"}:
                sources.append(OTX(max_indicators=args.max_indicators))
            if args.source in {"abuseipdb", "both"}:
                sources.append(AbuseIPDB(limit=args.abuse_limit))
        bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        transport = KafkaTransport(bootstrap)
        publisher = Publisher(transport)
        store = CollectorStore(args.state)
        store.drain(publisher, transport.flush)
        if events is not None:
            store.save(events)
            print(f"Published {store.drain(publisher, transport.flush)} fixture observations")
            return
        collector = ThreatCollector(sources, store, publisher, transport.flush)
        if args.consume_tasks:
            consumer = create_consumer(bootstrap)
        next_poll = 0.0
        while True:
            if time.monotonic() >= next_poll:
                collector.collect(hours=args.hours, max_pages=args.max_pages, country=args.country)
                next_poll = time.monotonic() + args.poll_seconds
                if args.once:
                    return
            if consumer is None:
                time.sleep(min(1, max(0, next_poll - time.monotonic())))
                continue
            message = consumer.poll(1)
            if message is None:
                continue
            if message.error():
                raise RuntimeError(str(message.error()))
            process_task(message, collector, publisher, transport.flush)
            consumer.commit(message=message, asynchronous=False)
    except KeyboardInterrupt:
        logging.info("collector stopped; pending outbox remains on disk")
    finally:
        if consumer is not None:
            consumer.close()
        if store is not None:
            store.close()
        for source in sources:
            source.http.close()


if __name__ == "__main__":
    main()
