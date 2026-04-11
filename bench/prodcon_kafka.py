#!/usr/bin/env python3
"""Kafka simultaneous producer+consumer — sustained load benchmark.

Runs NUM_PRODUCERS producer processes and NUM_CONSUMERS consumer threads
simultaneously on a dedicated topic for TEST_DURATION_SEC seconds.
Producers use separate OS processes (each with its own librdkafka Producer)
to fully utilise multiple CPU cores.
"""

import json
import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path

from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, NewTopic
from dotenv import dotenv_values
from tqdm import tqdm

_env = dotenv_values(Path(__file__).resolve().parent.parent / "kafka-client.env")


def _cfg(key):
    """Env var overrides .env file value."""
    return os.environ.get(key, _env[key])


BROKER = _cfg("KAFKA_BROKER")
TOPIC = _cfg("KAFKA_PRODCON_TOPIC")
DURATION = int(_cfg("TEST_DURATION_SEC"))
PAYLOAD_BYTES = int(_cfg("PAYLOAD_BYTES"))
PAYLOAD = b"x" * PAYLOAD_BYTES
NUM_PROD = int(_cfg("NUM_PRODUCERS"))
NUM_CONS = int(_cfg("NUM_CONSUMERS"))
KAFKA_QUEUE_MAX = int(os.environ.get("KAFKA_QUEUE_MAX", "100000"))
KAFKA_FLUSH_TIMEOUT = int(os.environ.get("KAFKA_FLUSH_TIMEOUT", "30"))
BATCH_SIZE = 500


def ensure_topic():
    """Create the prodcon benchmark topic if it doesn't exist."""
    admin = AdminClient({"bootstrap.servers": BROKER})
    num_parts = max(NUM_PROD, NUM_CONS)
    topic = NewTopic(TOPIC, num_partitions=num_parts, replication_factor=1)
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


def _producer_process(worker_id, stop_time, result_queue, count_dict):
    """Single producer OS process: sends as fast as possible until stop_time."""
    p = Producer(
        {
            "bootstrap.servers": BROKER,
            "acks": "all",
            "linger.ms": 5,
            "batch.num.messages": 1000,
            "queue.buffering.max.messages": KAFKA_QUEUE_MAX,
        }
    )

    payload = b"x" * PAYLOAD_BYTES
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
                sys.stderr.write(f"Producer {worker_id} delivery failed: {err}\n")
        else:
            acked += 1

    while time.monotonic() < stop_time:
        for _ in range(BATCH_SIZE):
            try:
                p.produce(TOPIC, payload, callback=on_delivery)
                enqueued += 1
            except BufferError:
                p.poll(0.1)
                try:
                    p.produce(TOPIC, payload, callback=on_delivery)
                    enqueued += 1
                except Exception:
                    enqueue_errors += 1
        p.poll(0)
        count_dict[worker_id] = acked

    remaining = p.flush(timeout=KAFKA_FLUSH_TIMEOUT)
    if remaining > 0:
        delivery_errors += remaining
        sys.stderr.write(
            f"Producer {worker_id} flush timed out with {remaining} undelivered messages\n"
        )
    wall = time.monotonic() - t0
    total_errors = enqueue_errors + delivery_errors

    result_queue.put(("producer", {
        "worker": worker_id,
        "sent": acked,
        "accepted": enqueued,
        "errors": total_errors,
        "enqueue_errors": enqueue_errors,
        "ack_errors": delivery_errors,
        "wall_sec": round(wall, 2),
        "avg_rate": round(acked / wall, 1),
    }))


def _consumer_thread(worker_id, stop_event, consumer_results, cons_lock, count_dict):
    """Single consumer thread: batch-consumes as fast as possible until stop_event."""
    c = Consumer(
        {
            "bootstrap.servers": BROKER,
            "group.id": "bench-prodcon-group",
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
        }
    )
    c.subscribe([TOPIC])

    consumed = 0
    t0 = time.monotonic()

    while not stop_event.is_set():
        msgs = c.consume(num_messages=500, timeout=0.5)
        if not msgs:
            continue
        for msg in msgs:
            if msg.error():
                continue
            consumed += 1
        count_dict[worker_id] = consumed

    # Drain remaining messages for a few seconds
    drain_deadline = time.monotonic() + 3
    while time.monotonic() < drain_deadline:
        msgs = c.consume(num_messages=500, timeout=0.5)
        if not msgs:
            break
        for msg in msgs:
            if msg.error():
                continue
            consumed += 1
        count_dict[worker_id] = consumed

    c.close()
    wall = time.monotonic() - t0

    with cons_lock:
        consumer_results.append({
            "worker": worker_id,
            "consumed": consumed,
            "wall_sec": round(wall, 2),
            "avg_rate": round(consumed / wall, 1) if wall > 0 else 0,
        })


def main():
    ensure_topic()
    time.sleep(2)

    manager = multiprocessing.Manager()
    prod_result_queue = manager.Queue()
    prod_counts = manager.dict()
    cons_counts = manager.dict()

    stop_event = threading.Event()
    consumer_results = []
    cons_lock = threading.Lock()

    # Start consumers first so they don't miss early messages
    cons_threads = []
    for i in range(NUM_CONS):
        t = threading.Thread(
            target=_consumer_thread,
            args=(i, stop_event, consumer_results, cons_lock, cons_counts),
            daemon=True,
        )
        cons_threads.append(t)
        t.start()

    time.sleep(2)  # Let consumers subscribe and rebalance

    # Start producer processes
    stop_time = time.monotonic() + DURATION
    prod_processes = []
    for i in range(NUM_PROD):
        p = multiprocessing.Process(
            target=_producer_process,
            args=(i, stop_time, prod_result_queue, prod_counts),
        )
        p.start()
        prod_processes.append(p)

    # Progress bar
    with tqdm(
        total=DURATION, unit="s", desc="Kafka prodcon", file=sys.stderr
    ) as pbar:
        for _ in range(DURATION):
            time.sleep(1)
            pbar.update(1)
            prod_total = sum(prod_counts.values()) if prod_counts else 0
            cons_total = sum(cons_counts.values()) if cons_counts else 0
            pbar.set_postfix(prod=f"{prod_total:,}", cons=f"{cons_total:,}")

    # Wait for producers to finish
    for p in prod_processes:
        p.join(timeout=KAFKA_FLUSH_TIMEOUT + 10)

    # Stop consumers
    stop_event.set()
    for t in cons_threads:
        t.join(timeout=10)

    # Collect producer results
    producer_results = []
    while not prod_result_queue.empty():
        _, data = prod_result_queue.get_nowait()
        producer_results.append(data)

    # Aggregate
    prod_total_sent = sum(r["sent"] for r in producer_results)
    prod_total_accepted = sum(r.get("accepted", r["sent"]) for r in producer_results)
    prod_total_wall = max(r["wall_sec"] for r in producer_results) if producer_results else 0
    prod_total_errors = sum(r["errors"] for r in producer_results)

    cons_total_consumed = sum(r["consumed"] for r in consumer_results)
    cons_total_wall = max(r["wall_sec"] for r in consumer_results) if consumer_results else 0

    output = {
        "broker": "kafka",
        "test_type": "prodcon",
        "num_producers": NUM_PROD,
        "num_consumers": NUM_CONS,
        "payload_bytes": PAYLOAD_BYTES,
        "duration_sec": DURATION,
        "producer": {
            "total_sent": prod_total_sent,
            "total_accepted": prod_total_accepted,
            "total_errors": prod_total_errors,
            "wall_sec": prod_total_wall,
            "aggregate_rate": round(prod_total_sent / prod_total_wall, 1) if prod_total_wall > 0 else 0,
            "per_worker": producer_results,
        },
        "consumer": {
            "total_consumed": cons_total_consumed,
            "wall_sec": cons_total_wall,
            "aggregate_rate": round(cons_total_consumed / cons_total_wall, 1) if cons_total_wall > 0 else 0,
            "per_worker": consumer_results,
        },
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
