#!/usr/bin/env python3
"""NATS JetStream producer — max throughput benchmark.

Runs NUM_PRODUCERS concurrent producer tasks for TEST_DURATION_SEC seconds.
No artificial rate cap — the broker's resource limits are the only throttle.
Uses asyncio with semaphore-bounded concurrency.
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
STREAM = _cfg("NATS_STREAM")
SUBJECT = _cfg("NATS_SUBJECT")
DURATION = int(_cfg("TEST_DURATION_SEC"))
PAYLOAD = b"x" * int(_cfg("PAYLOAD_BYTES"))
NUM_PROD = int(_cfg("NUM_PRODUCERS"))

results = []
_sent_counts: dict[int, int] = {}  # per-worker live counters for progress display


async def ensure_stream(js):
    """Create the JetStream stream if it doesn't exist."""
    try:
        await js.add_stream(
            name=STREAM,
            subjects=["bench.>"],
            retention="limits",
            max_bytes=10 * 1024 * 1024 * 1024,  # 10 GB
            storage="file",
        )
        print(f"Created stream {STREAM}", file=sys.stderr)
    except nats.js.errors.BadRequestError:
        print(f"Stream {STREAM} already exists", file=sys.stderr)
    except Exception as e:
        print(f"Warning creating stream: {e}", file=sys.stderr)


# Limit concurrent in-flight publishes per worker to avoid overwhelming the connection
MAX_INFLIGHT = 256
BATCH_SIZE = 500  # fire this many tasks per tight loop before yielding


async def producer_worker(worker_id, js):
    """Single producer coroutine: publishes as fast as possible for DURATION seconds."""
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
                    print(f"Worker {worker_id} publish error: {e}", file=sys.stderr)

    while time.monotonic() - t0 < DURATION:
        tasks = [asyncio.create_task(_pub()) for _ in range(BATCH_SIZE)]
        await asyncio.gather(*tasks)
        _sent_counts[worker_id] = sent

    wall = time.monotonic() - t0
    results.append(
        {
            "worker": worker_id,
            "sent": sent,
            "errors": errors,
            "wall_sec": round(wall, 2),
            "avg_rate": round(sent / wall, 1),
        }
    )


async def main():
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()

    await ensure_stream(js)
    await asyncio.sleep(1)

    async def _progress_monitor():
        with tqdm(
            total=DURATION, unit="s", desc="NATS throughput", file=sys.stderr
        ) as pbar:
            for _ in range(DURATION):
                await asyncio.sleep(1)
                pbar.update(1)
                pbar.set_postfix(msgs=f"{sum(_sent_counts.values()):,}")

    worker_tasks = [
        asyncio.create_task(producer_worker(i, js)) for i in range(NUM_PROD)
    ]
    monitor = asyncio.create_task(_progress_monitor())
    await asyncio.gather(*worker_tasks)
    monitor.cancel()
    await nc.close()

    total_sent = sum(r["sent"] for r in results)
    total_wall = max(r["wall_sec"] for r in results)
    total_errors = sum(r["errors"] for r in results)

    output = {
        "broker": "nats",
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
    asyncio.run(main())
