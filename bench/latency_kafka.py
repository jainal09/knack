#!/usr/bin/env python3
"""Kafka latency benchmark — AC-6.

Runs a single producer at 50% of peak throughput (from a prior throughput run)
and a consumer on the same process. Measures end-to-end publish-to-consume latency.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
from confluent_kafka import Consumer, KafkaError, Producer
from confluent_kafka.admin import AdminClient, NewTopic
from dotenv import dotenv_values
from latency_common import extract_latency_us, stamp_payload

_env = dotenv_values(Path(__file__).resolve().parent.parent / "kafka-client.env")


def _cfg(key):
    """Env var overrides .env file value."""
    return os.environ.get(key, _env[key])


BROKER = _cfg("KAFKA_BROKER")
TOPIC = _cfg("KAFKA_LATENCY_TOPIC")
PEAK_RATE = int(_cfg("PEAK_RATE"))
TARGET_RATE = PEAK_RATE // 2
DURATION = int(_cfg("LATENCY_DURATION_SEC"))
PAYLOAD_SZ = int(_cfg("PAYLOAD_BYTES"))

latencies = []
consumer_done = threading.Event()


def ensure_topic():
    admin = AdminClient({"bootstrap.servers": BROKER})
    topic = NewTopic(TOPIC, num_partitions=1, replication_factor=1)
    fs = admin.create_topics([topic])
    for t, f in fs.items():
        try:
            f.result()
        except Exception:
            pass


def consumer_thread():
    c = Consumer(
        {
            "bootstrap.servers": BROKER,
            "group.id": "bench-latency-consumer",
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
        }
    )
    c.subscribe([TOPIC])

    while not consumer_done.is_set():
        msg = c.poll(0.1)
        if msg is None:
            continue
        if msg.error():
            if msg.error().code() != KafkaError._PARTITION_EOF:
                print(f"Consumer error: {msg.error()}", file=sys.stderr)
            continue
        lat = extract_latency_us(msg.value())
        latencies.append(lat)

    c.close()


def main():
    ensure_topic()
    time.sleep(2)

    # Start consumer FIRST so it's subscribed before we produce
    ct = threading.Thread(target=consumer_thread, daemon=True)
    ct.start()
    time.sleep(2)  # let consumer join group

    p = Producer(
        {
            "bootstrap.servers": BROKER,
            "acks": "all",
            "linger.ms": 0,
        }
    )

    print(
        f"Producing at {TARGET_RATE} msg/s (50% of peak {PEAK_RATE}) for {DURATION}s ..."
    )
    t0 = time.monotonic()
    sent = 0
    while time.monotonic() - t0 < DURATION:
        batch_start = time.monotonic()
        for _ in range(TARGET_RATE):
            payload = stamp_payload(PAYLOAD_SZ)
            p.produce(TOPIC, payload)
            sent += 1
        p.flush()
        elapsed = time.monotonic() - batch_start
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)

    p.flush(timeout=10)
    time.sleep(3)  # drain
    consumer_done.set()
    ct.join(timeout=10)

    if not latencies:
        print("ERROR: no latency samples collected", file=sys.stderr)
        sys.exit(1)

    arr = np.array(latencies)
    result = {
        "broker": "kafka",
        "load_pct": 50,
        "target_rate": TARGET_RATE,
        "samples": len(latencies),
        "sent": sent,
        "p50_us": round(float(np.percentile(arr, 50)), 1),
        "p95_us": round(float(np.percentile(arr, 95)), 1),
        "p99_us": round(float(np.percentile(arr, 99)), 1),
        "p999_us": round(float(np.percentile(arr, 99.9)), 1),
        "max_us": round(float(np.max(arr)), 1),
    }
    print(json.dumps(result, indent=2))
    _results_dir = Path(__file__).resolve().parent.parent / "results"
    _results_dir.mkdir(exist_ok=True)
    with open(_results_dir / "kafka_latency.json", "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
