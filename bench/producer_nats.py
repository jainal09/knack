#!/usr/bin/env python3
"""NATS JetStream producer — sustained throughput benchmark.

Runs NUM_PRODUCERS concurrent producer tasks at BASELINE_RATE (per-producer)
for TEST_DURATION_SEC seconds with sync flush (durability parity with Kafka acks=all).
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import nats
from dotenv import dotenv_values

_env = dotenv_values(Path(__file__).resolve().parent.parent / "nats-client.env")


def _cfg(key):
    """Env var overrides .env file value."""
    return os.environ.get(key, _env[key])


NATS_URL = _cfg("NATS_URL")
STREAM = _cfg("NATS_STREAM")
SUBJECT = _cfg("NATS_SUBJECT")
RATE = int(_cfg("BASELINE_RATE"))
DURATION = int(_cfg("TEST_DURATION_SEC"))
PAYLOAD = b"x" * int(_cfg("PAYLOAD_BYTES"))
NUM_PROD = int(_cfg("NUM_PRODUCERS"))

results = []


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
        print(f"Created stream {STREAM}")
    except nats.js.errors.BadRequestError:
        print(f"Stream {STREAM} already exists")
    except Exception as e:
        print(f"Warning creating stream: {e}", file=sys.stderr)


async def producer_worker(worker_id, js):
    """Single producer coroutine: publishes RATE msgs/sec for DURATION seconds."""
    sent = 0
    errors = 0
    t0 = time.monotonic()

    while time.monotonic() - t0 < DURATION:
        batch_start = time.monotonic()
        for _ in range(RATE):
            try:
                await js.publish(SUBJECT, PAYLOAD)
                sent += 1
            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"Worker {worker_id} publish error: {e}", file=sys.stderr)
        elapsed = time.monotonic() - batch_start
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)

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

    tasks = [asyncio.create_task(producer_worker(i, js)) for i in range(NUM_PROD)]
    await asyncio.gather(*tasks)
    await nc.close()

    total_sent = sum(r["sent"] for r in results)
    total_wall = max(r["wall_sec"] for r in results)
    total_errors = sum(r["errors"] for r in results)

    output = {
        "broker": "nats",
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
    asyncio.run(main())
