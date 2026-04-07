#!/usr/bin/env bash
# env.sh — single source of truth for all benchmark parameters

export BENCH_CPUS="2.0"            # CPU cores allocated to each broker container
export BENCH_MEMORY="4g"           # Starting RAM cap (override per scenario)
export BENCH_DISK_TYPE="ssd"       # Document actual host disk type
export BENCH_DISK_SIZE="20g"

export PAYLOAD_BYTES=1024          # 1 KB message payload
export NUM_PRODUCERS=4             # Matches MAX_WORKERS=4
export BASELINE_RATE=5000          # Target msgs/sec per producer (total = RATE * NUM_PRODUCERS run in parallel)
export TEST_DURATION_SEC=600       # 10 min sustained run
export REPS=3                      # Number of repetitions; median used

export KAFKA_BROKER="localhost:9092"
export NATS_URL="nats://localhost:4222"
export KAFKA_TOPIC="bench"
export NATS_STREAM="BENCH"
export NATS_SUBJECT="bench.data"
