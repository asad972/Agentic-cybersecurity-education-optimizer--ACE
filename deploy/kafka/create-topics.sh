#!/bin/bash
# Creates every ACE topic. Topic names are a shared contract: see docs/ARCHITECTURE.md.
set -euo pipefail

BOOTSTRAP="${BOOTSTRAP:-kafka:29092}"
PARTITIONS="${PARTITIONS:-3}"
REPLICATION="${REPLICATION:-1}"   # 3 in the Kubernetes cluster

TOPICS=(
  raw.jobs
  raw.profiles
  raw.reports
  jobs.clean
  skills.needed
  skills.offered
  threats.events
  agg.top10_needed
  agg.needed_daily
  agg.offered_daily
  agg.threats_daily
  alerts.drift
  alerts.threats
  analysis.platforms
)

for topic in "${TOPICS[@]}"; do
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP" \
    --create --if-not-exists --topic "$topic" \
    --partitions "$PARTITIONS" --replication-factor "$REPLICATION"
done

echo "Topics ready:"
/opt/kafka/bin/kafka-topics.sh --bootstrap-server "$BOOTSTRAP" --list
