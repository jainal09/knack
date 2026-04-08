#!/usr/bin/env python3
"""NATS JetStream simultaneous producer+consumer — sustained load benchmark.

Runs NUM_PRODUCERS producer tasks and NUM_CONSUMERS consumer tasks in
**separate OS processes** (each with its own asyncio event loop) so that
high-throughput producers cannot starve the consumer coroutines.
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


async def ensure_stream():
    """Create (or purge) the JetStream stream so we start from zero."""
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()
    try:
        await js.delete_stream(STREAM)
        print(f"Deleted old stream {STREAM}", file=sys.stderr)
    except Exception:
        pass  # stream didn't exist
    try:
        await js.add_stream(
            name=STREAM,
            subjects=[SUBJECT],
            retention="limits",
            max_bytes=10 * 1024 * 1024 * 1024,  # 10 GB
            storage="file",
        )
        print(f"Created stream {STREAM}", file=sys.stderr)
    except Exception as e:
        print(f"Warning creating stream: {e}", file=sys.stderr)
    await nc.close()


# ---------------------------------------------------------------------------
# Producer process
# ---------------------------------------------------------------------------


async def _producer_worker(worker_id, stop_time):
    """Single producer coroutine inside the producer process."""
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()

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

    while time.monotonic() < stop_time:
        tasks = [asyncio.create_task(_pub()) for _ in range(BATCH_SIZE)]
        await asyncio.gather(*tasks)

    await nc.close()
    wall = time.monotonic() - t0
    return {
        "worker": worker_id,
        "sent": sent,
        "errors": errors,
        "wall_sec": round(wall, 2),
        "avg_rate": round(sent / wall, 1) if wall > 0 else 0,
    }


async def _run_producers(num, stop_time):
    """Run all producer workers in this process's event loop."""
    tasks = [asyncio.create_task(_producer_worker(i, stop_time)) for i in range(num)]
    return await asyncio.gather(*tasks)


def _producer_process(num, stop_time, result_queue, count_dict):
    """Entry-point for the producer OS process."""

    async def _main():
        # Live counter updater
        async def _counter_loop():
            while time.monotonic() < stop_time:
                await asyncio.sleep(0.5)

        tasks = []
        for i in range(num):
            tasks.append(
                asyncio.create_task(_producer_worker_counted(i, stop_time, count_dict))
            )
        await asyncio.gather(*tasks)
        for r in [t.result() if hasattr(t, "result") else t for t in tasks]:
            result_queue.put(("producer", r))

    asyncio.run(_main())


async def _producer_worker_counted(worker_id, stop_time, count_dict):
    """Producer worker that also updates shared counter dict."""
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()

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

    while time.monotonic() < stop_time:
        tasks = [asyncio.create_task(_pub()) for _ in range(BATCH_SIZE)]
        await asyncio.gather(*tasks)
        count_dict[worker_id] = sent

    await nc.close()
    wall = time.monotonic() - t0
    return {
        "worker": worker_id,
        "sent": sent,
        "errors": errors,
        "wall_sec": round(wall, 2),
        "avg_rate": round(sent / wall, 1) if wall > 0 else 0,
    }


# ---------------------------------------------------------------------------
# Consumer process
# ---------------------------------------------------------------------------


async def _consumer_worker_counted(worker_id, stop_time, count_dict):
    """Single consumer coroutine using push-based subscribe with a queue group.

    This is the NATS equivalent of Kafka's consumer-group pattern:
    multiple subscribers in the same queue group → server distributes
    messages round-robin across members, just like Kafka partitions
    are assigned to consumers in the same group.id.
    """
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()

    sub = await js.subscribe(
        SUBJECT,
        queue="bench-prodcon-group",
        durable="bench-prodcon-group",
        stream=STREAM,
    )

    consumed = 0
    t0 = time.monotonic()

    while time.monotonic() < stop_time:
        try:
            msg = await asyncio.wait_for(sub.next_msg(timeout=1.0), timeout=1.0)
            await msg.ack()
            consumed += 1
            if consumed % 500 == 0:
                count_dict[worker_id] = consumed
        except asyncio.TimeoutError:
            continue
        except Exception:
            break

    count_dict[worker_id] = consumed

    # Drain remaining messages
    drain_deadline = time.monotonic() + 3
    while time.monotonic() < drain_deadline:
        try:
            msg = await asyncio.wait_for(sub.next_msg(timeout=0.5), timeout=0.5)
            await msg.ack()
            consumed += 1
        except (asyncio.TimeoutError, Exception):
            break

    count_dict[worker_id] = consumed
    await sub.unsubscribe()
    await nc.close()

    wall = time.monotonic() - t0
    return {
        "worker": worker_id,
        "consumed": consumed,
        "wall_sec": round(wall, 2),
        "avg_rate": round(consumed / wall, 1) if wall > 0 else 0,
    }


def _consumer_process(num, stop_time, result_queue, count_dict):
    """Entry-point for the consumer OS process.

    Spawns NUM_CONSUMERS coroutines, each with its own NATS connection
    and push subscription in the same queue group — matching Kafka's
    4 consumer threads with a shared group.id.
    """

    async def _main():
        tasks = [
            asyncio.create_task(_consumer_worker_counted(i, stop_time, count_dict))
            for i in range(num)
        ]
        results = await asyncio.gather(*tasks)
        for r in results:
            result_queue.put(("consumer", r))

    asyncio.run(_main())


# ---------------------------------------------------------------------------
# Main orchestrator (runs in the original process)
# ---------------------------------------------------------------------------


def main():
    # Ensure stream exists before spawning children
    asyncio.run(ensure_stream())
    time.sleep(1)

    manager = multiprocessing.Manager()
    result_queue = manager.Queue()
    prod_counts = manager.dict()
    cons_counts = manager.dict()

    stop_time = time.monotonic() + DURATION + 4  # +4 s for startup slack

    # Start consumer process first so subscriptions are ready
    cp = multiprocessing.Process(
        target=_consumer_process,
        args=(NUM_CONS, stop_time, result_queue, cons_counts),
    )
    cp.start()
    time.sleep(2)  # Let consumers subscribe

    # Adjust stop_time for producers (they should stop a bit before consumers
    # so consumers can drain)
    prod_stop_time = time.monotonic() + DURATION
    pp = multiprocessing.Process(
        target=_producer_process,
        args=(NUM_PROD, prod_stop_time, result_queue, prod_counts),
    )
    pp.start()

    # Progress bar in the main process
    with tqdm(total=DURATION, unit="s", desc="NATS prodcon", file=sys.stderr) as pbar:
        for _ in range(DURATION):
            time.sleep(1)
            pbar.update(1)
            prod_total = sum(prod_counts.values()) if prod_counts else 0
            cons_total = sum(cons_counts.values()) if cons_counts else 0
            pbar.set_postfix(prod=f"{prod_total:,}", cons=f"{cons_total:,}")

    # Wait for both child processes to finish
    pp.join(timeout=30)
    cp.join(timeout=30)

    # Collect results from the queue
    producer_results = []
    consumer_results = []
    while not result_queue.empty():
        kind, data = result_queue.get_nowait()
        if kind == "producer":
            producer_results.append(data)
        else:
            consumer_results.append(data)

    # Aggregate producer results
    prod_total_sent = sum(r["sent"] for r in producer_results)
    prod_total_wall = (
        max(r["wall_sec"] for r in producer_results) if producer_results else 0
    )
    prod_total_errors = sum(r["errors"] for r in producer_results)

    # Aggregate consumer results
    cons_total_consumed = sum(r["consumed"] for r in consumer_results)
    cons_total_wall = (
        max(r["wall_sec"] for r in consumer_results) if consumer_results else 0
    )

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
            "aggregate_rate": round(prod_total_sent / prod_total_wall, 1)
            if prod_total_wall > 0
            else 0,
            "per_worker": producer_results,
        },
        "consumer": {
            "total_consumed": cons_total_consumed,
            "wall_sec": cons_total_wall,
            "aggregate_rate": round(cons_total_consumed / cons_total_wall, 1)
            if cons_total_wall > 0
            else 0,
            "per_worker": consumer_results,
            "per_worker": consumer_results,
        },
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
