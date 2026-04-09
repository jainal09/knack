#!/usr/bin/env bash
# env.sh — single source of truth for all benchmark parameters

export BENCH_CPUS="${BENCH_CPUS:-2.0}"            # CPU cores allocated to each broker container
export BENCH_MEMORY="${BENCH_MEMORY:-4g}"         # Starting RAM cap (override per scenario)
export BENCH_DISK_TYPE="${BENCH_DISK_TYPE:-ssd}"  # Document actual host disk type
export BENCH_DISK_SIZE="${BENCH_DISK_SIZE:-20g}"

export PAYLOAD_BYTES="${PAYLOAD_BYTES:-1024}"          # 1 KB message payload
export NUM_PRODUCERS="${NUM_PRODUCERS:-4}"             # Parallel producer threads/tasks
export TEST_DURATION_SEC="${TEST_DURATION_SEC:-600}"   # 10 min sustained run
export REPS="${REPS:-3}"                               # Number of repetitions; median used

export NUM_CONSUMERS="${NUM_CONSUMERS:-4}"
export SCALING_CPU_LEVELS="${SCALING_CPU_LEVELS:-4.0 3.0 2.0 1.5 1.0 0.5}"
export PREPOPULATE_COUNT="${PREPOPULATE_COUNT:-500000}"
export CLI_TOTAL_MESSAGES="${CLI_TOTAL_MESSAGES:-500000}"

# Client-side scaling (overridden by compute_budget when --budget is used)
export NATS_PENDING_SIZE="${NATS_PENDING_SIZE:-2097152}"      # 2MB default nats-py pending buffer
export NATS_MAX_MEM="${NATS_MAX_MEM:-256MB}"                  # JetStream max_mem in nats-server.conf
export NATS_MAX_FILE="${NATS_MAX_FILE:-10GB}"                 # JetStream max_file
export KAFKA_QUEUE_MAX="${KAFKA_QUEUE_MAX:-100000}"           # librdkafka queue.buffering.max.messages
export KAFKA_FLUSH_TIMEOUT="${KAFKA_FLUSH_TIMEOUT:-30}"       # seconds for producer.flush()
export NUM_PROC_GROUPS="${NUM_PROC_GROUPS:-1}"                # OS processes for multiprocess producers

export KAFKA_BROKER="localhost:9092"
export NATS_URL="nats://localhost:4222"
export KAFKA_TOPIC="bench"
export NATS_STREAM="BENCH"
export NATS_SUBJECT="bench.data"
