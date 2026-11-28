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

# The stages' results (agg.*) and alerts (alerts.*) get one partition. The stages
# write them without a Kafka key, so with several partitions one country's hours or
# days are spread over all of them, and a stage that is behind reads them out of
# order. S8 and S6 need each country's records in order (S8 drops an hour older than
# one it has seen). These topics hold one record per country per hour or day, so one
# partition is plenty. Kafka keeps order inside a partition.
partitions_for() {
  case "$1" in
    agg.*|alerts.*) echo 1 ;;
    *) echo "$PARTITIONS" ;;
  esac
}

# Topics that already have more partitions than wanted. Kafka cannot remove
# partitions, so they are listed at the end with how to recreate them.
wrong=()

# First column of every line except the header
while read -r topic; do
  [ -z "$topic" ] && continue
  want="$(partitions_for "$topic")"
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP" \
    --create --if-not-exists --topic "$topic" \
    --partitions "$want" --replication-factor "$REPLICATION" \
    --config "$TOPIC_CONFIG"
  # --if-not-exists leaves existing topics unchanged, so set it on those too.
  /opt/kafka/bin/kafka-configs.sh --bootstrap-server "$BOOTSTRAP" \
    --alter --entity-type topics --entity-name "$topic" --add-config "$TOPIC_CONFIG"
  have="$(/opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --describe --topic "$topic" \
          | sed -n 's/.*PartitionCount: *\([0-9]*\).*/\1/p' | head -n 1)"
  if [ -n "$have" ] && [ "$have" -gt "$want" ]; then
    wrong+=("$topic ($have partitions, needs $want)")
  fi
done < <(tail -n +2 "$TOPICS_FILE" | cut -d, -f1 | tr -d '\r')

echo "Topics ready:"
/opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --list

if [ "${#wrong[@]}" -gt 0 ]; then
  echo
  echo "WARNING: these topics were created with more partitions than they need, so stages"
  echo "may read one country's records out of order:"
  printf '  %s\n' "${wrong[@]}"
  echo "Kafka cannot remove partitions. To recreate them on a local stack, reset it with"
  echo "'docker compose down -v' and start it again (this deletes all Kafka and Cassandra data)."
fi
