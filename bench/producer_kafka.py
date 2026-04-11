#!/usr/bin/env python3
"""Kafka producer — max throughput benchmark.

Runs NUM_PRODUCERS concurrent producer processes for TEST_DURATION_SEC seconds
with acks=all. Each process has its own confluent-kafka Producer instance
(backed by librdkafka C threads) to fully utilise multiple CPU cores.
"""

import json
import multiprocessing
import os
import sys
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
PAYLOAD_BYTES = int(_cfg("PAYLOAD_BYTES"))
PAYLOAD = b"x" * PAYLOAD_BYTES
NUM_PROD = int(_cfg("NUM_PRODUCERS"))
KAFKA_QUEUE_MAX = int(os.environ.get("KAFKA_QUEUE_MAX", "100000"))
KAFKA_FLUSH_TIMEOUT = int(os.environ.get("KAFKA_FLUSH_TIMEOUT", "30"))
BATCH_SIZE = 1000  # messages per tight loop iteration before polling


def producer_worker(worker_id, stop_time, result_queue, count_dict):
    """Single producer process: sends as fast as possible until stop_time."""
    p = Producer(
        {
            "bootstrap.servers": BROKER,
            "acks": "all",
            "linger.ms": 5,
            "batch.num.messages": 1000,
            "queue.buffering.max.messages": KAFKA_QUEUE_MAX,
        }
    )

    enqueued = 0
    enqueue_errors = 0
    acked = 0
    delivery_errors = 0
    t0 = time.monotonic()

    def on_delivery(err, msg):
        nonlocal acked, delivery_errors
        if err is not None:
            delivery_errors += 1
            if delivery_errors <= 5:
                sys.stderr.write(f"Worker {worker_id} delivery failed: {err}\n")
        else:
            acked += 1

    while time.monotonic() < stop_time:
        for _ in range(BATCH_SIZE):
            try:
                p.produce(TOPIC, PAYLOAD, callback=on_delivery)
                enqueued += 1
            except BufferError:
                p.poll(0.1)
                try:
                    p.produce(TOPIC, PAYLOAD, callback=on_delivery)
                    enqueued += 1
                except Exception:
                    enqueue_errors += 1
        p.poll(0)
        count_dict[worker_id] = acked

    remaining = p.flush(timeout=KAFKA_FLUSH_TIMEOUT)
    if remaining > 0:
        delivery_errors += remaining
        sys.stderr.write(
            f"Worker {worker_id} flush timed out with {remaining} undelivered messages\n"
        )
    wall = time.monotonic() - t0
    total_errors = enqueue_errors + delivery_errors

    result_queue.put(
        {
            "worker": worker_id,
            "sent": acked,
            "accepted": enqueued,
            "errors": total_errors,
            "enqueue_errors": enqueue_errors,
            "ack_errors": delivery_errors,
            "wall_sec": round(wall, 2),
            "avg_rate": round(acked / wall, 1),
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

    manager = multiprocessing.Manager()
    result_queue = manager.Queue()
    count_dict = manager.dict()

    stop_time = time.monotonic() + DURATION

    processes = []
    for i in range(NUM_PROD):
        p = multiprocessing.Process(
            target=producer_worker,
            args=(i, stop_time, result_queue, count_dict),
        )
        p.start()
        processes.append(p)

    with tqdm(
        total=DURATION, unit="s", desc="Kafka throughput", file=sys.stderr
    ) as pbar:
        for _ in range(DURATION):
            time.sleep(1)
            pbar.update(1)
            pbar.set_postfix(msgs=f"{sum(count_dict.values()):,}")

    for p in processes:
        p.join(timeout=KAFKA_FLUSH_TIMEOUT + 10)

    results = []
    while not result_queue.empty():
        results.append(result_queue.get_nowait())

    total_sent = sum(r["sent"] for r in results)
    total_accepted = sum(r.get("accepted", r["sent"]) for r in results)
    total_wall = max(r["wall_sec"] for r in results) if results else 0
    total_errors = sum(r["errors"] for r in results)

    output = {
        "broker": "kafka",
        "num_producers": NUM_PROD,
        "payload_bytes": PAYLOAD_BYTES,
        "duration_sec": DURATION,
        "total_sent": total_sent,
        "total_accepted": total_accepted,
        "total_errors": total_errors,
        "wall_sec": total_wall,
        "aggregate_rate": round(total_sent / total_wall, 1) if total_wall > 0 else 0,
        "per_worker": results,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
