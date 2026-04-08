#!/usr/bin/env python3
"""Kafka producer — max throughput benchmark.

Runs NUM_PRODUCERS concurrent producer threads for TEST_DURATION_SEC seconds
with acks=all. No artificial rate cap — the broker's resource limits are the
only throttle.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

from confluent_kafka import Producer
from dotenv import dotenv_values
from tqdm import tqdm

_env = dotenv_values(Path(__file__).resolve().parent.parent / "kafka-client.env")


def _cfg(key):
    """Env var overrides .env file value."""
    return os.environ.get(key, _env[key])


BROKER = _cfg("KAFKA_BROKER")
TOPIC = _cfg("KAFKA_TOPIC")
DURATION = int(_cfg("TEST_DURATION_SEC"))
PAYLOAD = b"x" * int(_cfg("PAYLOAD_BYTES"))
NUM_PROD = int(_cfg("NUM_PRODUCERS"))
BATCH_SIZE = 1000  # messages per tight loop iteration before polling

results_lock = threading.Lock()
results = []
_sent_counts: dict[int, int] = {}  # per-worker live counters for progress display


def delivery_report(err, msg):
    if err is not None:
        sys.stderr.write(f"Delivery failed: {err}\n")


def producer_worker(worker_id):
    """Single producer thread: sends as fast as possible for DURATION seconds."""
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
        for _ in range(BATCH_SIZE):
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
        _sent_counts[worker_id] = sent

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
            print(f"Created topic {t}", file=sys.stderr)
        except Exception as e:
            if "TOPIC_ALREADY_EXISTS" in str(e):
                print(f"Topic {t} already exists", file=sys.stderr)
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

    with tqdm(
        total=DURATION, unit="s", desc="Kafka throughput", file=sys.stderr
    ) as pbar:
        for _ in range(DURATION):
            time.sleep(1)
            pbar.update(1)
            pbar.set_postfix(msgs=f"{sum(_sent_counts.values()):,}")

    for t in threads:
        t.join()

    total_sent = sum(r["sent"] for r in results)
    total_wall = max(r["wall_sec"] for r in results)
    total_errors = sum(r["errors"] for r in results)

    output = {
        "broker": "kafka",
        "num_producers": NUM_PROD,
        "payload_bytes": int(_cfg("PAYLOAD_BYTES")),
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
