#!/bin/sh
# nats-entrypoint.sh — Generate nats-server.conf from env vars at startup.
# This allows compute_budget() to scale JetStream max_mem with the container RAM.
NATS_MAX_MEM="${NATS_MAX_MEM:-256MB}"
NATS_MAX_FILE="${NATS_MAX_FILE:-10GB}"

cat > /tmp/nats-server.conf <<EOF
# NATS JetStream configuration — durability parity with Kafka acks=all
# sync_interval: 1s batches fsyncs (Kafka also doesn't fsync per message with acks=all)
listen: 0.0.0.0:4222
http: 0.0.0.0:8222

jetstream {
  store_dir: /data
  max_mem: ${NATS_MAX_MEM}
  max_file: ${NATS_MAX_FILE}
  sync_interval: 1s
}
EOF

exec nats-server -c /tmp/nats-server.conf
