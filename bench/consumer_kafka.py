#!/usr/bin/env python3
"""Kafka consumer — max throughput benchmark.

Pre-populates PREPOPULATE_COUNT messages into a dedicated topic using
multiprocess producers, then runs NUM_CONSUMERS concurrent consumers
across OS processes measuring pure consume speed.
"""

import asyncio
import json
import multiprocessing
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
PAYLOAD_BYTES = int(_cfg("PAYLOAD_BYTES"))
PAYLOAD = b"x" * PAYLOAD_BYTES
NUM_CONSUMERS = int(_cfg("NUM_CONSUMERS"))
PREPOPULATE_COUNT = int(os.environ.get("PREPOPULATE_COUNT", "500000"))
KAFKA_QUEUE_MAX = int(os.environ.get("KAFKA_QUEUE_MAX", "100000"))
KAFKA_FLUSH_TIMEOUT = int(os.environ.get("KAFKA_FLUSH_TIMEOUT", "30"))
NUM_PROC_GROUPS = int(os.environ.get("NUM_PROC_GROUPS", "1"))


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


def _prepop_worker(count, count_dict, proc_id):
    """Pre-populate a portion of messages in a separate OS process."""
    p = CProducer(
        {
            "bootstrap.servers": BROKER,
            "acks": "all",
            "linger.ms": 5,
            "batch.num.messages": 1000,
            "queue.buffering.max.messages": KAFKA_QUEUE_MAX,
        }
    )
    payload = b"x" * PAYLOAD_BYTES
    sent = 0
    errors = 0

    def _on_delivery(err, msg):
        nonlocal errors
        if err is not None:
            errors += 1

    for i in range(count):
        try:
            p.produce(TOPIC, payload, callback=_on_delivery)
            sent += 1
        except BufferError:
            p.poll(0.1)
            try:
                p.produce(TOPIC, payload, callback=_on_delivery)
                sent += 1
            except Exception:
                errors += 1
        if sent % 5000 == 0:
            p.poll(0)
            count_dict[proc_id] = sent

    p.flush(timeout=KAFKA_FLUSH_TIMEOUT)
    count_dict[proc_id] = sent


def prepopulate():
    """Fill the topic with PREPOPULATE_COUNT messages using multiple processes."""
    print(
        f"Pre-populating {PREPOPULATE_COUNT:,} messages into {TOPIC}...",
        file=sys.stderr,
    )
    num_groups = min(NUM_PROC_GROUPS, 4)
    per_group = PREPOPULATE_COUNT // num_groups
    remainder = PREPOPULATE_COUNT % num_groups

    manager = multiprocessing.Manager()
    count_dict = manager.dict()

    processes = []
    for i in range(num_groups):
        count = per_group + (1 if i < remainder else 0)
        p = multiprocessing.Process(
            target=_prepop_worker, args=(count, count_dict, i)
        )
        p.start()
        processes.append(p)

    with tqdm(
        total=PREPOPULATE_COUNT, unit="msg", desc="Pre-populating", file=sys.stderr
    ) as pbar:
        last = 0
        while any(p.is_alive() for p in processes):
            time.sleep(0.5)
            current = sum(count_dict.values())
            pbar.update(current - last)
            last = current

        current = sum(count_dict.values())
        pbar.update(current - last)

    for p in processes:
        p.join(timeout=KAFKA_FLUSH_TIMEOUT + 10)

    total = sum(count_dict.values())
    print(f"Pre-populated {total:,} messages", file=sys.stderr)
    return total


def _consumer_process(worker_id, partition, total_target, result_queue, count_dict):
    """Consumer process: reads from one partition as fast as possible."""

    async def _run():
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
                result = await consumer.getmany(tp, timeout_ms=2000)
                msgs = result.get(tp, [])
                if not msgs:
                    empty_polls += 1
                    if empty_polls >= 3:
                        break
                    continue
                empty_polls = 0
                if t0 is None:
                    t0 = time.monotonic()
                consumed += len(msgs)
                count_dict[worker_id] = consumed
        finally:
            await consumer.stop()

        if t0 is None:
            t0 = time.monotonic()
        wall = time.monotonic() - t0

        result_queue.put({
            "worker": worker_id,
            "partition": partition,
            "consumed": consumed,
            "wall_sec": round(wall, 2),
            "avg_rate": round(consumed / wall, 1) if wall > 0 else 0,
        })

    asyncio.run(_run())


def main():
    ensure_topic()
    time.sleep(2)

    prepopulated = prepopulate()
    time.sleep(1)

    print("Starting consumer benchmark...", file=sys.stderr)

    manager = multiprocessing.Manager()
    result_queue = manager.Queue()
    count_dict = manager.dict()

    per_consumer_target = prepopulated // NUM_CONSUMERS + 1

    processes = []
    for i in range(NUM_CONSUMERS):
        p = multiprocessing.Process(
            target=_consumer_process,
            args=(i, i, per_consumer_target, result_queue, count_dict),
        )
        p.start()
        processes.append(p)

    with tqdm(total=DURATION, unit="s", desc="Kafka consume", file=sys.stderr) as pbar:
        for _ in range(DURATION):
            time.sleep(1)
            pbar.update(1)
            total = sum(count_dict.values())
            pbar.set_postfix(msgs=f"{total:,}")

    for p in processes:
        p.join(timeout=30)

    results = []
    while not result_queue.empty():
        results.append(result_queue.get_nowait())

    total_consumed = sum(r["consumed"] for r in results)
    total_wall = max(r["wall_sec"] for r in results) if results else 0

    output = {
        "broker": "kafka",
        "test_type": "consumer",
        "num_consumers": NUM_CONSUMERS,
        "payload_bytes": PAYLOAD_BYTES,
        "duration_sec": DURATION,
        "prepopulated": prepopulated,
        "total_consumed": total_consumed,
        "wall_sec": total_wall,
        "aggregate_rate": round(total_consumed / total_wall, 1) if total_wall > 0 else 0,
        "per_worker": results,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
