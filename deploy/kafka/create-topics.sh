#!/bin/bash
# Creates every ACE topic listed in shared/schemas/topics.csv (the shared contract for topic names).
set -euo pipefail

BOOTSTRAP="${BOOTSTRAP:-kafka:29092}"
PARTITIONS="${PARTITIONS:-3}"
REPLICATION="${REPLICATION:-1}"   # 3 in the Kubernetes cluster
TOPICS_FILE="${TOPICS_FILE:-/topics.csv}"

# First column of every line except the header
tail -n +2 "$TOPICS_FILE" | cut -d, -f1 | tr -d '\r' | while read -r topic; do
  [ -z "$topic" ] && continue
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP" \
    --create --if-not-exists --topic "$topic" \
    --partitions "$PARTITIONS" --replication-factor "$REPLICATION"
done

echo "Topics ready:"
/opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --list
