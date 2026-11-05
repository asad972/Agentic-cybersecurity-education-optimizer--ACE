import argparse
import json
import logging
import os
import time

from ace import contracts

from .collector import CollectorStore, ThreatCollector
from .fixtures import fixture_events
from .runtime import EVENTS_TOPIC, TASKS_TOPIC, KafkaTransport, Publisher, create_consumer, KafkaDeliveryError, FatalKafkaError
from .sources import AbuseIPDB, OTX
from .utils import country_code, write_jsonl


LOG = logging.getLogger(__name__)


def process_task(message, collector, publisher, flush):
    """Acknowledge only after durable task enqueue or confirmed error delivery."""
    try:
        task = json.loads(message.value())
        collector.accept_task(task, message.key() or b"")
    except (contracts.InvalidMessage, ValueError, UnicodeError, TypeError) as error:
        rejected = contracts.invalid_record(TASKS_TOPIC, "threat-agent", str(error),
            (message.value() or b"").decode("utf-8", errors="replace"))
        publisher.publish(contracts.INVALID_TOPIC, rejected)
        flush()


def poll_task(consumer):
    message = consumer.poll(1)
    if message is not None and message.error():
        error = message.error()
        if error.fatal():
            raise FatalKafkaError(str(error))
        LOG.warning("Recoverable Kafka consumer error: %s", error)
        return None
    return message


def run_loop(collector, consumer, publisher, flush, *, hours, max_pages, country,
             poll_seconds, once=False, retention_days=30, sleep=time.sleep):
    from confluent_kafka import KafkaException
    pending_message = None
    next_prune = 0.0
    while True:
        try:
            # Each source persists its own next poll and retry deadline in SQLite.
            collector.collect(hours=hours, max_pages=max_pages, country=country,
                              poll_seconds=poll_seconds)
            collector.run_pending_tasks()
            if time.monotonic() >= next_prune:
                collector.store.prune(retention_days)
                next_prune = time.monotonic() + 3600
            if once:
                return
            if consumer is not None:
                if pending_message is None:
                    pending_message = poll_task(consumer)
                if pending_message is not None:
                    process_task(pending_message, collector, publisher, flush)
                    consumer.commit(message=pending_message, asynchronous=False)
                    pending_message = None
        except (KafkaDeliveryError, KafkaException) as error:
            if isinstance(error, KafkaException) and error.args[0].fatal():
                raise FatalKafkaError(str(error)) from error
            LOG.warning("Kafka temporarily unavailable; durable work will retry (%s)", type(error).__name__)
            if once:
                raise
            sleep(5)
            continue
        sleep(1)


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
    parser.add_argument("--retention-days", type=int, default=30, help="Retain acknowledged IDs for this many days")
    args = parser.parse_args(argv)
    if args.retention_days < 1:
        parser.error("retention-days must be positive")
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
        if events is not None:
            store.drain(publisher, transport.flush)
            store.save(events)
            print(f"Published {store.drain(publisher, transport.flush)} fixture observations")
            return
        collector = ThreatCollector(sources, store, publisher, transport.flush)
        if args.consume_tasks:
            consumer = create_consumer(bootstrap)
        run_loop(collector, consumer, publisher, transport.flush, hours=args.hours,
                 max_pages=args.max_pages, country=args.country, poll_seconds=args.poll_seconds,
                 once=args.once, retention_days=args.retention_days)
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
