#!/usr/bin/env python3
"""NATS JetStream simultaneous producer+consumer — sustained load benchmark.

Runs NUM_PRODUCERS producer tasks and NUM_CONSUMERS consumer tasks
simultaneously via asyncio.gather for TEST_DURATION_SEC seconds.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import nats
from dotenv import dotenv_values
from tqdm import tqdm

_env = dotenv_values(Path(__file__).resolve().parent.parent / "nats-client.env")


def _cfg(key):
    """Env var overrides .env file value."""
    return os.environ.get(key, _env[key])


NATS_URL = _cfg("NATS_URL")
STREAM = _cfg("NATS_PRODCON_STREAM")
SUBJECT = _cfg("NATS_PRODCON_SUBJECT")
DURATION = int(_cfg("TEST_DURATION_SEC"))
PAYLOAD = b"x" * int(_cfg("PAYLOAD_BYTES"))
NUM_PROD = int(_cfg("NUM_PRODUCERS"))
NUM_CONS = int(_cfg("NUM_CONSUMERS"))

MAX_INFLIGHT = 256
BATCH_SIZE = 500

producer_results = []
consumer_results = []
_prod_counts: dict[int, int] = {}
_cons_counts: dict[int, int] = {}


async def ensure_stream(js):
    """Create the JetStream stream if it doesn't exist."""
    try:
        await js.add_stream(
            name=STREAM,
            subjects=[SUBJECT],
            retention="limits",
            max_bytes=10 * 1024 * 1024 * 1024,  # 10 GB
            storage="file",
        )
        print(f"Created stream {STREAM}", file=sys.stderr)
    except nats.js.errors.BadRequestError:
        print(f"Stream {STREAM} already exists", file=sys.stderr)
    except Exception as e:
        print(f"Warning creating stream: {e}", file=sys.stderr)


async def producer_worker(worker_id, js, stop_event):
    """Single producer coroutine: publishes as fast as possible until stop."""
    sent = 0
    errors = 0
    sem = asyncio.Semaphore(MAX_INFLIGHT)
    t0 = time.monotonic()

    async def _pub():
        nonlocal sent, errors
        async with sem:
            try:
                await js.publish(SUBJECT, PAYLOAD)
                sent += 1
            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"Producer {worker_id} error: {e}", file=sys.stderr)

    while not stop_event.is_set():
        tasks = [asyncio.create_task(_pub()) for _ in range(BATCH_SIZE)]
        await asyncio.gather(*tasks)
        _prod_counts[worker_id] = sent

    wall = time.monotonic() - t0
    producer_results.append(
        {
            "worker": worker_id,
            "sent": sent,
            "errors": errors,
            "wall_sec": round(wall, 2),
            "avg_rate": round(sent / wall, 1) if wall > 0 else 0,
        }
    )


async def consumer_worker(worker_id, js, stop_event):
    """Single consumer coroutine: pulls as fast as possible until stop."""
    sub = await js.pull_subscribe(
        SUBJECT,
        durable=f"bench-prodcon-consumer-{worker_id}",
        stream=STREAM,
    )

    consumed = 0
    t0 = time.monotonic()

    while not stop_event.is_set():
        try:
            msgs = await asyncio.wait_for(sub.fetch(batch=500), timeout=0.5)
        except asyncio.TimeoutError:
            continue
        except Exception:
            break

        for msg in msgs:
            await msg.ack()
            consumed += 1
        _cons_counts[worker_id] = consumed

    # Drain remaining messages
    drain_deadline = time.monotonic() + 3
    while time.monotonic() < drain_deadline:
        try:
            msgs = await asyncio.wait_for(sub.fetch(batch=500), timeout=0.5)
            for msg in msgs:
                await msg.ack()
                consumed += 1
            _cons_counts[worker_id] = consumed
        except (asyncio.TimeoutError, Exception):
            break

    wall = time.monotonic() - t0
    await sub.unsubscribe()

    consumer_results.append(
        {
            "worker": worker_id,
            "consumed": consumed,
            "wall_sec": round(wall, 2),
            "avg_rate": round(consumed / wall, 1) if wall > 0 else 0,
        }
    )


async def main():
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()

    await ensure_stream(js)
    await asyncio.sleep(1)

    stop_event = asyncio.Event()

    # Start consumers first
    cons_tasks = [
        asyncio.create_task(consumer_worker(i, js, stop_event))
        for i in range(NUM_CONS)
    ]
    await asyncio.sleep(2)  # Let consumers subscribe

    # Start producers
    prod_tasks = [
        asyncio.create_task(producer_worker(i, js, stop_event))
        for i in range(NUM_PROD)
    ]

    # Progress monitor
    async def _progress_monitor():
        with tqdm(
            total=DURATION, unit="s", desc="NATS prodcon", file=sys.stderr
        ) as pbar:
            for _ in range(DURATION):
                await asyncio.sleep(1)
                pbar.update(1)
                prod_total = sum(_prod_counts.values())
                cons_total = sum(_cons_counts.values())
                pbar.set_postfix(prod=f"{prod_total:,}", cons=f"{cons_total:,}")

    monitor = asyncio.create_task(_progress_monitor())

    # Wait for duration
    await asyncio.sleep(DURATION)
    stop_event.set()

    await asyncio.gather(*prod_tasks)
    await asyncio.gather(*cons_tasks)
    monitor.cancel()

    await nc.close()

    # Aggregate producer results
    prod_total_sent = sum(r["sent"] for r in producer_results)
    prod_total_wall = max(r["wall_sec"] for r in producer_results) if producer_results else 0
    prod_total_errors = sum(r["errors"] for r in producer_results)

    # Aggregate consumer results
    cons_total_consumed = sum(r["consumed"] for r in consumer_results)
    cons_total_wall = max(r["wall_sec"] for r in consumer_results) if consumer_results else 0

    output = {
        "broker": "nats",
        "test_type": "prodcon",
        "num_producers": NUM_PROD,
        "num_consumers": NUM_CONS,
        "payload_bytes": int(_cfg("PAYLOAD_BYTES")),
        "duration_sec": DURATION,
        "producer": {
            "total_sent": prod_total_sent,
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
    asyncio.run(main())
