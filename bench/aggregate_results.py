#!/usr/bin/env python3
"""aggregate_results.py — AC-8/AC-9: Merge all results into OMB-compatible JSON + decision."""

import json
import os
import statistics
import sys
from pathlib import Path

from dotenv import dotenv_values

_project_root = Path(__file__).resolve().parent.parent
_kafka_env = dotenv_values(_project_root / "kafka-client.env")
_nats_env = dotenv_values(_project_root / "nats-client.env")

RESULTS_DIR = os.environ.get("RESULTS_DIR", str(_project_root / "results"))
REPS = int(os.environ.get("REPS", "3"))


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Warning: could not load {path}: {e}", file=sys.stderr)
        return None


def get_median_throughput(broker):
    """Get median aggregate_rate across throughput runs."""
    rates = []
    for i in range(1, REPS + 1):
        path = os.path.join(RESULTS_DIR, f"{broker}_throughput_run{i}.json")
        data = load_json(path)
        if data and "aggregate_rate" in data:
            rates.append(data["aggregate_rate"])
    return statistics.median(rates) if rates else None


def get_median_consumer_throughput(broker):
    """Get median aggregate_rate across consumer throughput runs."""
    rates = []
    for i in range(1, REPS + 1):
        path = os.path.join(RESULTS_DIR, f"{broker}_consumer_run{i}.json")
        data = load_json(path)
        if data and "aggregate_rate" in data:
            rates.append(data["aggregate_rate"])
    return statistics.median(rates) if rates else None


def get_min_viable_ram(broker):
    """Find lowest memory level where broker passed."""
    levels = ["512m", "1g", "2g", "4g"]
    min_viable = None
    for mem in levels:
        path = os.path.join(RESULTS_DIR, f"{broker}_mem_{mem}.json")
        data = load_json(path)
        if data and data.get("status") == "PASS":
            min_viable = mem
            break
    return min_viable


def build_report():
    report = {
        "metadata": {
            "benchmark": "kafka-vs-nats-jetstream",
            "hardware": {
                "cpus": _kafka_env["BENCH_CPUS"],
                "memory": _kafka_env["BENCH_MEMORY"],
                "disk_type": _kafka_env["BENCH_DISK_TYPE"],
            },
            "payload_bytes": int(_kafka_env["PAYLOAD_BYTES"]),
            "num_producers": int(_kafka_env["NUM_PRODUCERS"]),
            "num_consumers": int(_kafka_env.get("NUM_CONSUMERS", "4")),
        },
        "idle_footprint": {},
        "startup_recovery": {},
        "throughput": {},
        "consumer_throughput": {},
        "prodcon": {},
        "latency": {},
        "memory_stress": {},
        "resource_scaling": {},
        "decision": {},
    }

    # --- Idle footprint (AC-3) ---
    for broker in ["kafka", "nats"]:
        data = load_json(os.path.join(RESULTS_DIR, f"{broker}_idle_stats.json"))
        if data:
            report["idle_footprint"][broker] = data

    # --- Startup / Recovery (AC-4) ---
    for broker in ["kafka", "nats"]:
        path = os.path.join(RESULTS_DIR, f"{broker}_startup.json")
        if os.path.exists(path):
            entries = []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            report["startup_recovery"][broker] = entries

    # --- Throughput (AC-5) ---
    for broker in ["kafka", "nats"]:
        runs = []
        for i in range(1, REPS + 1):
            data = load_json(
                os.path.join(RESULTS_DIR, f"{broker}_throughput_run{i}.json")
            )
            if data:
                runs.append(data)
        median_rate = get_median_throughput(broker)
        report["throughput"][broker] = {
            "runs": runs,
            "median_aggregate_rate": median_rate,
        }

    # --- Consumer Throughput ---
    for broker in ["kafka", "nats"]:
        runs = []
        for i in range(1, REPS + 1):
            data = load_json(
                os.path.join(RESULTS_DIR, f"{broker}_consumer_run{i}.json")
            )
            if data:
                runs.append(data)
        median_rate = get_median_consumer_throughput(broker)
        report["consumer_throughput"][broker] = {
            "runs": runs,
            "median_aggregate_rate": median_rate,
        }

    # --- Simultaneous Producer+Consumer ---
    for broker in ["kafka", "nats"]:
        data = load_json(os.path.join(RESULTS_DIR, f"{broker}_prodcon.json"))
        if data:
            report["prodcon"][broker] = data

    # --- Latency (AC-6) ---
    for broker in ["kafka", "nats"]:
        data = load_json(os.path.join(RESULTS_DIR, f"{broker}_latency.json"))
        if data:
            report["latency"][broker] = data

    # --- Memory stress (AC-7) ---
    for broker in ["kafka", "nats"]:
        levels = {}
        for mem in ["4g", "2g", "1g", "512m"]:
            data = load_json(os.path.join(RESULTS_DIR, f"{broker}_mem_{mem}.json"))
            if data:
                levels[mem] = data
        min_ram = get_min_viable_ram(broker)
        report["memory_stress"][broker] = {
            "levels": levels,
            "min_viable_ram": min_ram,
        }

    # --- Resource Scaling ---
    for broker in ["kafka", "nats"]:
        data = load_json(os.path.join(RESULTS_DIR, f"{broker}_scaling.json"))
        if data:
            report["resource_scaling"][broker] = data

    # --- Decision (AC-9) ---
    kafka_min = get_min_viable_ram("kafka")
    nats_min = get_min_viable_ram("nats")
    kafka_median = get_median_throughput("kafka")
    nats_median = get_median_throughput("nats")

    # CLI throughput is the fair broker-vs-broker comparison (both tools are
    # optimised by their respective maintainers). Python client numbers reflect
    # client library design, not broker capacity.
    kafka_cli = load_json(os.path.join(RESULTS_DIR, "kafka_cli_throughput.json"))
    nats_cli = load_json(os.path.join(RESULTS_DIR, "nats_cli_throughput.json"))
    kafka_cli_tp = kafka_cli.get("msgs_per_sec") if kafka_cli else None
    nats_cli_tp = nats_cli.get("msgs_per_sec") if nats_cli else None

    kafka_lat = load_json(os.path.join(RESULTS_DIR, "kafka_latency.json"))
    nats_lat = load_json(os.path.join(RESULTS_DIR, "nats_latency.json"))

    recommendation = "INCONCLUSIVE"
    reasoning = []

    # Memory thresholds
    ram_order = {"512m": 512, "1g": 1024, "2g": 2048, "4g": 4096}

    kafka_mb = ram_order.get(kafka_min, 9999) if kafka_min else 9999
    nats_mb = ram_order.get(nats_min, 9999) if nats_min else 9999

    if kafka_min and nats_min:
        reasoning.append(
            f"Kafka min viable RAM: {kafka_min}, NATS min viable RAM: {nats_min}"
        )

    # Latency comparison
    kafka_p99 = kafka_lat.get("p99_us", 0) if kafka_lat else 0
    nats_p99 = nats_lat.get("p99_us", 0) if nats_lat else 0
    lat_ratio = (kafka_p99 / nats_p99) if nats_p99 > 0 else None
    kafka_lat_wins = lat_ratio is not None and lat_ratio < 1.2
    nats_lat_wins = lat_ratio is not None and lat_ratio > 5.0

    if lat_ratio is not None:
        reasoning.append(
            f"Kafka p99={kafka_p99:,.1f}us, NATS p99={nats_p99:,.1f}us (ratio={lat_ratio:.2f}x)"
        )

    # Throughput comparison — prefer CLI numbers (fair broker comparison)
    kafka_tp_wins = False
    nats_tp_wins = False
    if kafka_cli_tp and nats_cli_tp:
        tp_ratio = kafka_cli_tp / nats_cli_tp
        reasoning.append(
            f"CLI Throughput — Kafka: {kafka_cli_tp:,.1f} msg/s, NATS: {nats_cli_tp:,.1f} msg/s (ratio={tp_ratio:.2f}x)"
        )
        kafka_tp_wins = tp_ratio > 1.5
        nats_tp_wins = tp_ratio < 0.67
    elif kafka_median and nats_median:
        # Fallback to Python client numbers if CLI data unavailable
        tp_ratio = kafka_median / nats_median
        reasoning.append(
            f"Python Throughput (fallback) — Kafka: {kafka_median:,.1f} msg/s, NATS: {nats_median:,.1f} msg/s (ratio={tp_ratio:.2f}x)"
        )
        kafka_tp_wins = tp_ratio > 1.5
        nats_tp_wins = tp_ratio < 0.67
    if kafka_median and nats_median:
        reasoning.append(
            f"Python Client Throughput — Kafka: {kafka_median:,.1f} msg/s, NATS: {nats_median:,.1f} msg/s "
            f"(reflects client library design, not broker capacity)"
        )

    # Consumer throughput (informational)
    kafka_cons_median = get_median_consumer_throughput("kafka")
    nats_cons_median = get_median_consumer_throughput("nats")
    if kafka_cons_median and nats_cons_median:
        cons_ratio = kafka_cons_median / nats_cons_median
        reasoning.append(
            f"Consumer Throughput — Kafka: {kafka_cons_median:,.1f} msg/s, "
            f"NATS: {nats_cons_median:,.1f} msg/s (ratio={cons_ratio:.2f}x)"
        )

    # ProdCon (informational)
    kafka_pc = load_json(os.path.join(RESULTS_DIR, "kafka_prodcon.json"))
    nats_pc = load_json(os.path.join(RESULTS_DIR, "nats_prodcon.json"))
    if kafka_pc and nats_pc:
        kp = kafka_pc.get("producer", {}).get("aggregate_rate", 0)
        kc = kafka_pc.get("consumer", {}).get("aggregate_rate", 0)
        np_ = nats_pc.get("producer", {}).get("aggregate_rate", 0)
        nc_ = nats_pc.get("consumer", {}).get("aggregate_rate", 0)
        if kp and np_:
            reasoning.append(
                f"ProdCon — Kafka: P={kp:,.1f}/C={kc:,.1f} msg/s, "
                f"NATS: P={np_:,.1f}/C={nc_:,.1f} msg/s"
            )

    # Decision tree
    # 1. Hard RAM constraint: one broker can't run at all on restricted hardware
    if nats_mb <= 512 and kafka_mb > 2048:
        recommendation = "MIGRATE_TO_NATS"
        reasoning.append("NATS runs at <=512MB while Kafka needs >2GB")
    elif kafka_mb <= 512 and nats_mb > 2048:
        recommendation = "KEEP_KAFKA"
        reasoning.append("Kafka runs at <=512MB while NATS needs >2GB")
    # 2. Both fit in RAM — compare latency + throughput
    elif kafka_lat_wins and kafka_tp_wins:
        recommendation = "KEEP_KAFKA"
        reasoning.append("Kafka wins both throughput and latency")
    elif nats_lat_wins and nats_tp_wins:
        recommendation = "MIGRATE_TO_NATS"
        reasoning.append("NATS wins both throughput and latency")
    elif nats_lat_wins and kafka_tp_wins:
        recommendation = "TRADEOFF"
        reasoning.append(
            "Kafka leads throughput but NATS leads latency — "
            "choose based on workload priority"
        )
    elif kafka_lat_wins and nats_tp_wins:
        recommendation = "TRADEOFF"
        reasoning.append(
            "NATS leads throughput but Kafka leads latency — "
            "choose based on workload priority"
        )
    # 3. One dimension wins, other is close
    elif kafka_tp_wins and not nats_lat_wins:
        recommendation = "KEEP_KAFKA"
        reasoning.append("Kafka wins throughput; latency is comparable")
    elif nats_tp_wins and not kafka_lat_wins:
        recommendation = "MIGRATE_TO_NATS"
        reasoning.append("NATS wins throughput; latency is comparable")
    elif nats_lat_wins and not kafka_tp_wins:
        recommendation = "MIGRATE_TO_NATS"
        reasoning.append("NATS wins latency; throughput is comparable")
    elif kafka_lat_wins and not nats_tp_wins:
        recommendation = "KEEP_KAFKA"
        reasoning.append("Kafka wins latency; throughput is comparable")

    report["decision"] = {
        "recommendation": recommendation,
        "kafka_min_viable_ram": kafka_min,
        "nats_min_viable_ram": nats_min,
        "reasoning": reasoning,
    }

    return report


def print_summary(report):
    """Print a human-readable summary table."""
    d = report["decision"]
    print("\n" + "=" * 60)
    print("  BENCHMARK DECISION SUMMARY")
    print("=" * 60)
    print(f"  Recommendation:       {d.get('recommendation', 'N/A')}")
    print(f"  Kafka min viable RAM: {d.get('kafka_min_viable_ram', 'N/A')}")
    print(f"  NATS min viable RAM:  {d.get('nats_min_viable_ram', 'N/A')}")
    print()

    # Producer throughput
    for b in ["kafka", "nats"]:
        t = report.get("throughput", {}).get(b, {})
        rate = t.get("median_aggregate_rate")
        if rate:
            print(f"  {b.upper()} Producer Throughput:  {rate:,.0f} msg/s")

    # Consumer throughput
    for b in ["kafka", "nats"]:
        ct = report.get("consumer_throughput", {}).get(b, {})
        rate = ct.get("median_aggregate_rate")
        if rate:
            print(f"  {b.upper()} Consumer Throughput:  {rate:,.0f} msg/s")

    # ProdCon
    for b in ["kafka", "nats"]:
        pc = report.get("prodcon", {}).get(b, {})
        if pc:
            pr = pc.get("producer", {}).get("aggregate_rate")
            cr = pc.get("consumer", {}).get("aggregate_rate")
            if pr and cr:
                print(f"  {b.upper()} ProdCon:             P={pr:,.0f} / C={cr:,.0f} msg/s")

    print()
    for line in d.get("reasoning", []):
        print(f"  - {line}")
    print("=" * 60)


def main():
    report = build_report()

    out_path = os.path.join(RESULTS_DIR, "full_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Full report written to {out_path}")

    print_summary(report)


if __name__ == "__main__":
    main()
