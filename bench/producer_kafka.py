#!/usr/bin/env python3
"""Kafka producer — sustained throughput benchmark.

Runs NUM_PRODUCERS concurrent producer threads at BASELINE_RATE (per-producer)
for TEST_DURATION_SEC seconds with acks=all.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

from confluent_kafka import Producer
from dotenv import dotenv_values

_env = dotenv_values(Path(__file__).resolve().parent.parent / "kafka-client.env")


def _cfg(key):
    """Env var overrides .env file value."""
    return os.environ.get(key, _env[key])


BROKER = _cfg("KAFKA_BROKER")
TOPIC = _cfg("KAFKA_TOPIC")
RATE = int(_cfg("BASELINE_RATE"))
DURATION = int(_cfg("TEST_DURATION_SEC"))
PAYLOAD = b"x" * int(_cfg("PAYLOAD_BYTES"))
NUM_PROD = int(_cfg("NUM_PRODUCERS"))

results_lock = threading.Lock()
results = []


def delivery_report(err, msg):
    if err is not None:
        sys.stderr.write(f"Delivery failed: {err}\n")


def producer_worker(worker_id):
    """Single producer thread: sends RATE msgs/sec for DURATION seconds."""
    p = Producer(
        {
            "bootstrap.servers": BROKER,
            "acks": "all",
            "linger.ms": 5,
            "batch.num.messages": 1000,
            "queue.buffering.max.messages": 100000,
        }
    )

    sent = 0
    errors = 0
    t0 = time.monotonic()

    while time.monotonic() - t0 < DURATION:
        batch_start = time.monotonic()
        for _ in range(RATE):
            try:
                p.produce(TOPIC, PAYLOAD, callback=delivery_report)
                sent += 1
            except BufferError:
                p.poll(0.1)
                try:
                    p.produce(TOPIC, PAYLOAD, callback=delivery_report)
                    sent += 1
                except Exception:
                    errors += 1
        p.poll(0)
        elapsed = time.monotonic() - batch_start
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)

    p.flush(timeout=30)
    wall = time.monotonic() - t0

    with results_lock:
        results.append(
            {
                "worker": worker_id,
                "sent": sent,
                "errors": errors,
                "wall_sec": round(wall, 2),
                "avg_rate": round(sent / wall, 1),
            }
        )


def ensure_topic():
    """Create the benchmark topic if it doesn't exist."""
    from confluent_kafka.admin import AdminClient, NewTopic

    admin = AdminClient({"bootstrap.servers": BROKER})
    topic = NewTopic(TOPIC, num_partitions=NUM_PROD, replication_factor=1)
    fs = admin.create_topics([topic])
    for t, f in fs.items():
        try:
            f.result()
            print(f"Created topic {t}")
        except Exception as e:
            if "TOPIC_ALREADY_EXISTS" in str(e):
                print(f"Topic {t} already exists")
            else:
                print(f"Warning creating topic: {e}", file=sys.stderr)


def main():
    ensure_topic()
    time.sleep(2)

    threads = []
    for i in range(NUM_PROD):
        t = threading.Thread(target=producer_worker, args=(i,), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    total_sent = sum(r["sent"] for r in results)
    total_wall = max(r["wall_sec"] for r in results)
    total_errors = sum(r["errors"] for r in results)

    output = {
        "broker": "kafka",
        "num_producers": NUM_PROD,
        "target_rate_per_producer": RATE,
        "payload_bytes": int(_env["PAYLOAD_BYTES"]),
        "duration_sec": DURATION,
        "total_sent": total_sent,
        "total_errors": total_errors,
        "wall_sec": total_wall,
        "aggregate_rate": round(total_sent / total_wall, 1),
        "per_worker": results,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
