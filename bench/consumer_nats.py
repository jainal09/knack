#!/usr/bin/env python3
"""NATS JetStream consumer — max throughput benchmark.

Pre-populates a stream with PREPOPULATE_COUNT messages, then runs
NUM_CONSUMERS concurrent pull subscribers measuring pure consume speed.
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
STREAM = _cfg("NATS_CONSUMER_STREAM")
SUBJECT = _cfg("NATS_CONSUMER_SUBJECT")
DURATION = int(_cfg("TEST_DURATION_SEC"))
PAYLOAD = b"x" * int(_cfg("PAYLOAD_BYTES"))
NUM_CONSUMERS = int(_cfg("NUM_CONSUMERS"))
PREPOPULATE_COUNT = int(os.environ.get("PREPOPULATE_COUNT", "500000"))

_consumed_counts: dict[int, int] = {}  # per-worker live counters for progress

# Pre-population concurrency controls
MAX_INFLIGHT = 256
BATCH_SIZE = 500


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


async def prepopulate(js):
    """Fill the stream with PREPOPULATE_COUNT messages."""
    print(
        f"Pre-populating {PREPOPULATE_COUNT:,} messages into {STREAM}...",
        file=sys.stderr,
    )
    sent = 0
    errors = 0
    sem = asyncio.Semaphore(MAX_INFLIGHT)

    async def _pub():
        nonlocal sent, errors
        async with sem:
            try:
                await js.publish(SUBJECT, PAYLOAD)
                sent += 1
            except Exception:
                errors += 1

    with tqdm(
        total=PREPOPULATE_COUNT, unit="msg", desc="Pre-populating", file=sys.stderr
    ) as pbar:
        remaining = PREPOPULATE_COUNT
        while remaining > 0:
            batch = min(BATCH_SIZE, remaining)
            tasks = [asyncio.create_task(_pub()) for _ in range(batch)]
            await asyncio.gather(*tasks)
            remaining -= batch
            pbar.update(batch)

    print(f"Pre-populated {sent:,} messages ({errors} errors)", file=sys.stderr)
    return sent


async def consumer_worker(worker_id, js, total_target, stop_event):
    """Single pull subscriber: fetches as fast as possible."""
    sub = await js.pull_subscribe(
        SUBJECT,
        durable=f"bench-consumer-{worker_id}",
        stream=STREAM,
    )

    consumed = 0
    t0 = None
    deadline = time.monotonic() + DURATION
    empty_polls = 0

    while not stop_event.is_set():
        try:
            msgs = await asyncio.wait_for(sub.fetch(batch=500), timeout=2.0)
        except asyncio.TimeoutError:
            empty_polls += 1
            if empty_polls >= 3:
                break  # No more messages left
            continue
        except Exception:
            break

        empty_polls = 0
        for msg in msgs:
            if t0 is None:
                t0 = time.monotonic()
            await msg.ack()
            consumed += 1
        _consumed_counts[worker_id] = consumed

        if time.monotonic() > deadline:
            break
        if consumed >= total_target:
            break

    if t0 is None:
        t0 = time.monotonic()
    wall = time.monotonic() - t0

    await sub.unsubscribe()

    return {
        "worker": worker_id,
        "consumed": consumed,
        "wall_sec": round(wall, 2),
        "avg_rate": round(consumed / wall, 1) if wall > 0 else 0,
    }


async def main():
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()

    await ensure_stream(js)
    await asyncio.sleep(1)

    prepopulated = await prepopulate(js)
    await asyncio.sleep(1)

    print("Starting consumer benchmark...", file=sys.stderr)
    stop_event = asyncio.Event()
    per_consumer_target = prepopulated // NUM_CONSUMERS + 1

    worker_tasks = [
        asyncio.create_task(consumer_worker(i, js, per_consumer_target, stop_event))
        for i in range(NUM_CONSUMERS)
    ]

    # Progress monitor
    async def _progress_monitor():
        with tqdm(
            total=DURATION, unit="s", desc="NATS consume", file=sys.stderr
        ) as pbar:
            for _ in range(DURATION):
                await asyncio.sleep(1)
                pbar.update(1)
                total = sum(_consumed_counts.values())
                pbar.set_postfix(msgs=f"{total:,}")

    monitor = asyncio.create_task(_progress_monitor())
    worker_results = await asyncio.gather(*worker_tasks)
    monitor.cancel()
    stop_event.set()

    await nc.close()

    total_consumed = sum(r["consumed"] for r in worker_results)
    total_wall = max(r["wall_sec"] for r in worker_results) if worker_results else 0

    output = {
        "broker": "nats",
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
    asyncio.run(main())
