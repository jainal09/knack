#!/usr/bin/env python3
"""Kafka consumer — max throughput benchmark.

Pre-populates PREPOPULATE_COUNT messages into a dedicated topic, then runs
NUM_CONSUMERS concurrent aiokafka consumers measuring pure consume speed.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from confluent_kafka import Producer as CProducer
from confluent_kafka.admin import AdminClient, NewTopic
from dotenv import dotenv_values
from tqdm import tqdm

_env = dotenv_values(Path(__file__).resolve().parent.parent / "kafka-client.env")


def _cfg(key):
    """Env var overrides .env file value."""
    return os.environ.get(key, _env[key])


BROKER = _cfg("KAFKA_BROKER")
TOPIC = _cfg("KAFKA_CONSUMER_TOPIC")
DURATION = int(_cfg("TEST_DURATION_SEC"))
PAYLOAD = b"x" * int(_cfg("PAYLOAD_BYTES"))
NUM_CONSUMERS = int(_cfg("NUM_CONSUMERS"))
PREPOPULATE_COUNT = int(os.environ.get("PREPOPULATE_COUNT", "500000"))

_consumed_counts: dict[int, int] = {}  # per-worker live counters for progress


def ensure_topic():
    """Create the consumer benchmark topic if it doesn't exist."""
    admin = AdminClient({"bootstrap.servers": BROKER})
    topic = NewTopic(TOPIC, num_partitions=NUM_CONSUMERS, replication_factor=1)
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


def prepopulate():
    """Fill the topic with PREPOPULATE_COUNT messages using confluent-kafka Producer."""
    print(
        f"Pre-populating {PREPOPULATE_COUNT:,} messages into {TOPIC}...",
        file=sys.stderr,
    )
    p = CProducer(
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

    def _on_delivery(err, msg):
        nonlocal errors
        if err is not None:
            errors += 1

    with tqdm(
        total=PREPOPULATE_COUNT, unit="msg", desc="Pre-populating", file=sys.stderr
    ) as pbar:
        for i in range(PREPOPULATE_COUNT):
            try:
                p.produce(TOPIC, PAYLOAD, callback=_on_delivery)
                sent += 1
            except BufferError:
                p.poll(0.1)
                try:
                    p.produce(TOPIC, PAYLOAD, callback=_on_delivery)
                    sent += 1
                except Exception:
                    errors += 1
            if sent % 5000 == 0:
                p.poll(0)
                pbar.update(5000)

        # Final flush
        p.flush(timeout=60)
        pbar.update(sent - pbar.n)

    print(
        f"Pre-populated {sent:,} messages ({errors} errors)", file=sys.stderr
    )
    return sent


async def consumer_worker(worker_id, partition, total_target):
    """Single aiokafka consumer: reads from one partition as fast as possible."""
    from aiokafka import AIOKafkaConsumer, TopicPartition

    tp = TopicPartition(TOPIC, partition)
    consumer = AIOKafkaConsumer(
        bootstrap_servers=BROKER,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        auto_commit_interval_ms=1000,
        max_poll_records=500,
        fetch_max_bytes=10 * 1024 * 1024,  # 10 MB
    )
    await consumer.start()
    consumer.assign([tp])
    await consumer.seek_to_beginning(tp)

    consumed = 0
    t0 = None
    deadline = time.monotonic() + DURATION
    empty_polls = 0

    try:
        while time.monotonic() < deadline and consumed < total_target:
            # Use getmany with timeout so we never block forever
            result = await consumer.getmany(tp, timeout_ms=2000)
            msgs = result.get(tp, [])
            if not msgs:
                empty_polls += 1
                if empty_polls >= 3:
                    break  # No more messages left
                continue
            empty_polls = 0
            if t0 is None:
                t0 = time.monotonic()
            consumed += len(msgs)
            _consumed_counts[worker_id] = consumed
    finally:
        await consumer.stop()

    if t0 is None:
        t0 = time.monotonic()
    wall = time.monotonic() - t0

    return {
        "worker": worker_id,
        "partition": partition,
        "consumed": consumed,
        "wall_sec": round(wall, 2),
        "avg_rate": round(consumed / wall, 1) if wall > 0 else 0,
    }


async def run_consumers(prepopulated):
    """Launch NUM_CONSUMERS async tasks and monitor progress."""
    per_consumer_target = prepopulated // NUM_CONSUMERS + 1

    tasks = [
        asyncio.create_task(consumer_worker(i, i, per_consumer_target))
        for i in range(NUM_CONSUMERS)
    ]

    # Progress monitor
    async def _progress_monitor():
        with tqdm(
            total=DURATION, unit="s", desc="Kafka consume", file=sys.stderr
        ) as pbar:
            for _ in range(DURATION):
                await asyncio.sleep(1)
                pbar.update(1)
                total = sum(_consumed_counts.values())
                pbar.set_postfix(msgs=f"{total:,}")

    monitor = asyncio.create_task(_progress_monitor())
    results = await asyncio.gather(*tasks)
    monitor.cancel()

    return results


def main():
    ensure_topic()
    time.sleep(2)

    prepopulated = prepopulate()
    time.sleep(1)

    print("Starting consumer benchmark...", file=sys.stderr)
    worker_results = asyncio.run(run_consumers(prepopulated))

    total_consumed = sum(r["consumed"] for r in worker_results)
    total_wall = max(r["wall_sec"] for r in worker_results) if worker_results else 0

    output = {
        "broker": "kafka",
        "test_type": "consumer",
        "num_consumers": NUM_CONSUMERS,
        "payload_bytes": int(_cfg("PAYLOAD_BYTES")),
        "duration_sec": DURATION,
        "prepopulated": prepopulated,
        "total_consumed": total_consumed,
        "wall_sec": total_wall,
        "aggregate_rate": round(total_consumed / total_wall, 1) if total_wall > 0 else 0,
        "per_worker": worker_results,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
