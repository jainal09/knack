#!/usr/bin/env python3
"""NATS JetStream producer — max throughput benchmark.

Runs NUM_PRODUCERS concurrent producer tasks spread across NUM_PROC_GROUPS
OS processes, each with its own asyncio event loop and NATS connection.
This ensures all available CPU cores are utilised instead of being
bottlenecked by a single Python process.
"""

import asyncio
import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

import nats
from dotenv import dotenv_values
from tqdm import tqdm

from bench.nats_async_publish import (
    JetStreamAsyncPublisher,
    NATS_JS_API_TIMEOUT,
    NATS_JS_ASYNC_MAX_PENDING,
)

_env = dotenv_values(Path(__file__).resolve().parent.parent / "nats-client.env")


def _cfg(key):
    """Env var overrides .env file value."""
    return os.environ.get(key, _env[key])


NATS_URL = _cfg("NATS_URL")
STREAM = _cfg("NATS_STREAM")
SUBJECT = _cfg("NATS_SUBJECT")
DURATION = int(_cfg("TEST_DURATION_SEC"))
PAYLOAD_BYTES = int(_cfg("PAYLOAD_BYTES"))
PAYLOAD = b"x" * PAYLOAD_BYTES
NUM_PROD = int(_cfg("NUM_PRODUCERS"))
NATS_PENDING_SIZE = int(os.environ.get("NATS_PENDING_SIZE", str(2 * 1024 * 1024)))
NUM_PROC_GROUPS = int(os.environ.get("NUM_PROC_GROUPS", "1"))

BATCH_SIZE = 500  # fire this many tasks per tight loop before yielding


async def ensure_stream():
    """Create the JetStream stream if it doesn't exist."""
    nc = await nats.connect(NATS_URL, pending_size=NATS_PENDING_SIZE)
    js = nc.jetstream(
        timeout=NATS_JS_API_TIMEOUT,
        publish_async_max_pending=NATS_JS_ASYNC_MAX_PENDING,
    )
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
    await nc.close()


async def _producer_worker(worker_id, stop_time, count_dict):
    """Single producer coroutine: publishes as fast as possible until stop_time."""
    nc = await nats.connect(NATS_URL, pending_size=NATS_PENDING_SIZE)
    js = nc.jetstream(
        timeout=NATS_JS_API_TIMEOUT,
        publish_async_max_pending=NATS_JS_ASYNC_MAX_PENDING,
    )
    publisher = JetStreamAsyncPublisher(
        js,
        SUBJECT,
        payload=PAYLOAD,
        max_pending=NATS_JS_ASYNC_MAX_PENDING,
        error_prefix=f"Worker {worker_id}",
    )
    t0 = time.monotonic()

    while time.monotonic() < stop_time:
        await publisher.submit_many(BATCH_SIZE)
        count_dict[worker_id] = publisher.sent

    await publisher.flush()
    await nc.close()
    wall = time.monotonic() - t0
    return {
        "worker": worker_id,
        "sent": publisher.sent,
        "accepted": publisher.accepted,
        "errors": publisher.total_errors,
        "enqueue_errors": publisher.enqueue_errors,
        "ack_errors": publisher.ack_errors,
        "wall_sec": round(wall, 2),
        "avg_rate": round(publisher.sent / wall, 1) if wall > 0 else 0,
    }


def _process_entry(worker_ids, stop_time, result_queue, count_dict):
    """Entry-point for each OS process. Runs len(worker_ids) coroutines."""

    async def _main():
        tasks = [
            asyncio.create_task(_producer_worker(wid, stop_time, count_dict))
            for wid in worker_ids
        ]
        results = await asyncio.gather(*tasks)
        for r in results:
            result_queue.put(r)

    asyncio.run(_main())


def main():
    # Ensure stream exists before spawning children
    asyncio.run(ensure_stream())
    time.sleep(1)

    manager = multiprocessing.Manager()
    result_queue = manager.Queue()
    count_dict = manager.dict()

    stop_time = time.monotonic() + DURATION

    # Split workers across OS processes
    num_groups = min(NUM_PROC_GROUPS, NUM_PROD)
    groups = [[] for _ in range(num_groups)]
    for i in range(NUM_PROD):
        groups[i % num_groups].append(i)

    processes = []
    for group in groups:
        p = multiprocessing.Process(
            target=_process_entry,
            args=(group, stop_time, result_queue, count_dict),
        )
        p.start()
        processes.append(p)

    # Progress bar in the main process
    with tqdm(total=DURATION, unit="s", desc="NATS throughput", file=sys.stderr) as pbar:
        for _ in range(DURATION):
            time.sleep(1)
            pbar.update(1)
            pbar.set_postfix(msgs=f"{sum(count_dict.values()):,}")

    for p in processes:
        p.join(timeout=30)

    # Collect results
    results = []
    while not result_queue.empty():
        results.append(result_queue.get_nowait())

    total_sent = sum(r["sent"] for r in results)
    total_accepted = sum(r.get("accepted", r["sent"]) for r in results)
    total_wall = max(r["wall_sec"] for r in results) if results else 0
    total_errors = sum(r["errors"] for r in results)

    output = {
        "broker": "nats",
        "num_producers": NUM_PROD,
        "num_processes": num_groups,
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
