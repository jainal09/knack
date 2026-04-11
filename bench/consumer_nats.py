#!/usr/bin/env python3
"""NATS JetStream consumer — max throughput benchmark.

Pre-populates a stream with PREPOPULATE_COUNT messages using multiprocess
producers, then runs NUM_CONSUMERS concurrent pull subscribers across
multiple OS processes measuring pure consume speed.
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
STREAM = _cfg("NATS_CONSUMER_STREAM")
SUBJECT = _cfg("NATS_CONSUMER_SUBJECT")
DURATION = int(_cfg("TEST_DURATION_SEC"))
PAYLOAD_BYTES = int(_cfg("PAYLOAD_BYTES"))
PAYLOAD = b"x" * PAYLOAD_BYTES
NUM_CONSUMERS = int(_cfg("NUM_CONSUMERS"))
PREPOPULATE_COUNT = int(os.environ.get("PREPOPULATE_COUNT", "500000"))
NATS_PENDING_SIZE = int(os.environ.get("NATS_PENDING_SIZE", str(2 * 1024 * 1024)))
NUM_PROC_GROUPS = int(os.environ.get("NUM_PROC_GROUPS", "1"))

BATCH_SIZE = 500


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
    await nc.close()


def _prepop_process(count, count_dict, proc_id):
    """Pre-populate a portion of messages in a separate OS process."""

    async def _run():
        nc = await nats.connect(NATS_URL, pending_size=NATS_PENDING_SIZE)
        js = nc.jetstream(
            timeout=NATS_JS_API_TIMEOUT,
            publish_async_max_pending=NATS_JS_ASYNC_MAX_PENDING,
        )
        payload = b"x" * PAYLOAD_BYTES
        publisher = JetStreamAsyncPublisher(
            js,
            SUBJECT,
            payload=payload,
            max_pending=NATS_JS_ASYNC_MAX_PENDING,
            error_prefix=f"Pre-populate worker {proc_id}",
        )

        remaining = count
        while remaining > 0:
            batch = min(BATCH_SIZE, remaining)
            await publisher.submit_many(batch, payload)
            remaining -= batch
            count_dict[proc_id] = publisher.sent

        await publisher.flush()
        await nc.close()

    asyncio.run(_run())


def prepopulate():
    """Fill the stream with PREPOPULATE_COUNT messages using multiple processes."""
    print(
        f"Pre-populating {PREPOPULATE_COUNT:,} messages into {STREAM}...",
        file=sys.stderr,
    )
    num_groups = min(NUM_PROC_GROUPS, 4)  # cap prepop parallelism
    per_group = PREPOPULATE_COUNT // num_groups
    remainder = PREPOPULATE_COUNT % num_groups

    manager = multiprocessing.Manager()
    count_dict = manager.dict()

    processes = []
    for i in range(num_groups):
        count = per_group + (1 if i < remainder else 0)
        p = multiprocessing.Process(
            target=_prepop_process, args=(count, count_dict, i)
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
        p.join(timeout=30)

    total = sum(count_dict.values())
    print(f"Pre-populated {total:,} messages", file=sys.stderr)
    return total


def _consumer_process(worker_ids, total_target_per_worker, result_queue, count_dict):
    """Consumer process entry-point. Runs multiple consumer coroutines."""

    async def _consumer_worker(worker_id):
        nc = await nats.connect(NATS_URL, pending_size=NATS_PENDING_SIZE)
        js = nc.jetstream()

        sub = await js.pull_subscribe(
            SUBJECT, durable=f"bench-consumer-{worker_id}", stream=STREAM,
        )

        consumed = 0
        t0 = None
        deadline = time.monotonic() + DURATION
        empty_polls = 0

        while True:
            try:
                msgs = await asyncio.wait_for(sub.fetch(batch=500), timeout=2.0)
            except asyncio.TimeoutError:
                empty_polls += 1
                if empty_polls >= 3:
                    break
                continue
            except Exception:
                break

            empty_polls = 0
            for msg in msgs:
                if t0 is None:
                    t0 = time.monotonic()
                await msg.ack()
                consumed += 1
            count_dict[worker_id] = consumed

            if time.monotonic() > deadline:
                break
            if consumed >= total_target_per_worker:
                break

        if t0 is None:
            t0 = time.monotonic()
        wall = time.monotonic() - t0
        await sub.unsubscribe()
        await nc.close()

        return {
            "worker": worker_id,
            "consumed": consumed,
            "wall_sec": round(wall, 2),
            "avg_rate": round(consumed / wall, 1) if wall > 0 else 0,
        }

    async def _main():
        tasks = [asyncio.create_task(_consumer_worker(wid)) for wid in worker_ids]
        results = await asyncio.gather(*tasks)
        for r in results:
            result_queue.put(r)

    asyncio.run(_main())


def main():
    asyncio.run(ensure_stream())
    time.sleep(1)

    prepopulated = prepopulate()
    time.sleep(1)

    print("Starting consumer benchmark...", file=sys.stderr)

    manager = multiprocessing.Manager()
    result_queue = manager.Queue()
    count_dict = manager.dict()

    per_consumer_target = prepopulated // NUM_CONSUMERS + 1

    # Split consumers across OS processes
    num_groups = min(NUM_PROC_GROUPS, NUM_CONSUMERS)
    groups = [[] for _ in range(num_groups)]
    for i in range(NUM_CONSUMERS):
        groups[i % num_groups].append(i)

    processes = []
    for group in groups:
        p = multiprocessing.Process(
            target=_consumer_process,
            args=(group, per_consumer_target, result_queue, count_dict),
        )
        p.start()
        processes.append(p)

    # Progress monitor
    with tqdm(total=DURATION, unit="s", desc="NATS consume", file=sys.stderr) as pbar:
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
        "broker": "nats",
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
