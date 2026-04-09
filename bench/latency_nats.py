#!/usr/bin/env python3
"""NATS JetStream latency benchmark — AC-6.

Runs a single producer at 50% of peak throughput and an async consumer.
Measures end-to-end publish-to-consume latency.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import nats
import numpy as np
from dotenv import dotenv_values
from latency_common import extract_latency_us, stamp_payload
from tqdm import tqdm

_env = dotenv_values(Path(__file__).resolve().parent.parent / "nats-client.env")


def _cfg(key):
    """Env var overrides .env file value."""
    return os.environ.get(key, _env[key])


NATS_URL = _cfg("NATS_URL")
STREAM = _cfg("NATS_LATENCY_STREAM")
SUBJECT = _cfg("NATS_LATENCY_SUBJECT")
PEAK_RATE = int(_cfg("PEAK_RATE"))
TARGET_RATE = PEAK_RATE // 2
DURATION = int(_cfg("LATENCY_DURATION_SEC"))
PAYLOAD_SZ = int(_cfg("PAYLOAD_BYTES"))
NATS_PENDING_SIZE = int(os.environ.get("NATS_PENDING_SIZE", str(2 * 1024 * 1024)))

latencies = []


async def main():
    nc = await nats.connect(NATS_URL, pending_size=NATS_PENDING_SIZE)
    js = nc.jetstream()

    # Ensure stream
    try:
        await js.add_stream(name=STREAM, subjects=["bench.latency"], storage="file")
    except Exception:
        pass

    # Subscribe (push-based for low latency)
    async def on_msg(msg):
        lat = extract_latency_us(msg.data)
        latencies.append(lat)
        await msg.ack()

    sub = await js.subscribe(SUBJECT, stream=STREAM, ordered_consumer=True)
    # Use a pull-based approach for ordered consumers instead
    # Actually, let's use a simple push subscription
    await sub.unsubscribe()

    sub = await js.subscribe(SUBJECT, durable="bench-lat-consumer", stream=STREAM)

    # Start a background task to drain messages
    async def consumer_loop():
        try:
            async for msg in sub.messages:
                lat = extract_latency_us(msg.data)
                latencies.append(lat)
                await msg.ack()
        except Exception:
            pass

    consumer_task = asyncio.create_task(consumer_loop())
    await asyncio.sleep(2)

    print(
        f"Producing at {TARGET_RATE} msg/s (50% of peak {PEAK_RATE}) for {DURATION}s ...",
        file=sys.stderr,
    )
    t0 = time.monotonic()
    sent = 0
    with tqdm(total=DURATION, unit="s", desc="NATS latency", file=sys.stderr) as pbar:
        while time.monotonic() - t0 < DURATION:
            batch_start = time.monotonic()
            for _ in range(TARGET_RATE):
                payload = stamp_payload(PAYLOAD_SZ)
                await js.publish(SUBJECT, payload)
                sent += 1
            elapsed = time.monotonic() - batch_start
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)
            pbar.update(1)
            pbar.set_postfix(msgs=f"{sent:,}")

    await asyncio.sleep(3)  # drain
    consumer_task.cancel()

    await nc.close()

    if not latencies:
        print("ERROR: no latency samples collected", file=sys.stderr)
        sys.exit(1)

    arr = np.array(latencies)
    result = {
        "broker": "nats",
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
    with open(_results_dir / "nats_latency.json", "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
