#!/bin/bash
# Creates every ACE topic listed in shared/schemas/topics.csv (the shared contract for topic names).
set -euo pipefail

BOOTSTRAP="${BOOTSTRAP:-kafka:29092}"
PARTITIONS="${PARTITIONS:-3}"
REPLICATION="${REPLICATION:-1}"   # 3 in the Kubernetes cluster
TOPICS_FILE="${TOPICS_FILE:-/topics.csv}"

# Retention counts from when a message arrives, not from its timestamp. Stages write
# results with their event time as the timestamp (e.g. the end of the day they count),
# so replayed history would otherwise be older than the retention and deleted at once.
TOPIC_CONFIG="message.timestamp.type=LogAppendTime"

# First column of every line except the header
tail -n +2 "$TOPICS_FILE" | cut -d, -f1 | tr -d '\r' | while read -r topic; do
  [ -z "$topic" ] && continue
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP" \
    --create --if-not-exists --topic "$topic" \
    --partitions "$PARTITIONS" --replication-factor "$REPLICATION" \
    --config "$TOPIC_CONFIG"
  # --if-not-exists leaves existing topics unchanged, so set it on those too.
  /opt/kafka/bin/kafka-configs.sh --bootstrap-server "$BOOTSTRAP" \
    --alter --entity-type topics --entity-name "$topic" --add-config "$TOPIC_CONFIG"
done

echo "Topics ready:"
/opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --list
